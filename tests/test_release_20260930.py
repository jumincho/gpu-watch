"""Behavioral regressions for the 2026-09-30 release, without live credentials."""
import ast
import builtins
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import server
from gpu_watch.artificial_analysis import ArtificialAnalysisIndex


class OwnerAttributionTests(unittest.TestCase):
    def test_unreadable_status_does_not_invent_root_owner(self):
        tree = ast.parse(server.REMOTE_PROBE)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'process_metadata')
        namespace = {'os': os, 'pwd': types.SimpleNamespace(getpwuid=mock.Mock()),
                     'process_start_identity': lambda pid: '1000'}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<metadata>', 'exec'), namespace)
        with mock.patch.object(os, 'stat', return_value=types.SimpleNamespace(st_uid=0)), \
             mock.patch.object(builtins, 'open', side_effect=PermissionError):
            observed = namespace['process_metadata'](42)
        self.assertEqual(observed['user'], '')
        self.assertEqual(observed['_proc_start_ticks'], '1000')
        namespace['pwd'].getpwuid.assert_not_called()

    def test_partial_cohort_remains_busy_but_is_not_charged_to_known_user(self):
        with tempfile.TemporaryDirectory() as folder:
            store = server.Store(Path(folder) / 'test.sqlite3')
            config = server.load_config()
            config['hosts'] = [{'name': 'test', 'lab': 'nll', 'expected_gpu_count': 1}]
            gpu = {'index': 0, 'uuid': 'GPU-test', 'name': 'GPU', 'memory_total': 8192,
                   'memory_used': 64, 'utilization': 0, 'temperature': 30,
                   'processes': [
                       {'pid': 1, 'user': 'alice', 'process_name': 'python', 'start_identity': '1000'},
                       {'pid': 2, 'user': '', 'process_name': 'python', 'start_identity': '2000'}]}
            for ts in (100, 110):
                with mock.patch('server.now_ts', return_value=ts):
                    store.apply_host_payload('test', 'test', [(gpu, True)], expected_gpu_indices={0})
            with mock.patch('server.now_ts', return_value=120):
                host = store.snapshot(config)['hosts'][0]
            self.assertTrue(host['gpus'][0]['busy'])
            self.assertEqual(host['usage']['busy_seconds'], 20)
            self.assertFalse(any(row['user'] == 'alice' for row in host['usage']['top_users']))
            self.assertFalse(any(row['user'] == 'alice' for row in host['recent_usage']['top_users']))
            with closing(store.connect()) as conn:
                interval = conn.execute('select users,seconds from gpu_usage_interval').fetchone()
                self.assertIsNone(interval['users'])
                self.assertEqual(interval['seconds'], 10)

    def test_known_multi_user_cohort_splits_once_per_user(self):
        cohort = [{'user': 'alice'}, {'user': 'alice'}, {'user': 'bob'}]
        seconds = {}
        server.add_user_seconds(seconds, server.attributable_process_users(cohort), 60)
        self.assertEqual(seconds, {'alice': 30, 'bob': 30})

    def test_unknown_small_process_is_preserved_after_gpu_pid_revalidation(self):
        original_open, original_stat = builtins.open, os.stat
        queries = []
        class FakePopen:
            returncode = 0
            def __init__(self, command, **kwargs): self.command = command
            def communicate(self, timeout=None):
                queries.append(self.command)
                if self.command[0] == 'nvidia-smi':
                    if any('--query-gpu=' in arg for arg in self.command):
                        return b'0, Test GPU, GPU-A, 0, 64, 8192, 30, 580.0\n', b''
                    return b'42, GPU-A, 64, python\n', b''
                if self.command[0] == 'ps':
                    return b'42 ? Wed Sep 30 00:00:00 2026 python\n', b''
                if self.command[0] == 'hostname': return b'test\n', b''
                raise AssertionError(self.command)
            def kill(self): pass
        def fake_open(path, mode='r', *args, **kwargs):
            name = str(path)
            if name == '/proc/42/stat':
                fields = ['S'] + ['0'] * 18 + ['1000']
                return io.StringIO('42 (python) ' + ' '.join(fields))
            if name == '/proc/42/status': raise PermissionError
            if name == '/proc/42/comm': return io.StringIO('python\n')
            if name == '/proc/42/cmdline': return io.BytesIO(b'python\0train.py\0--token\0secret\0')
            if name == '/proc/42/cgroup': return io.StringIO('')
            return original_open(path, mode, *args, **kwargs)
        def fake_stat(path, *args, **kwargs):
            if str(path) == '/proc/42': return types.SimpleNamespace(st_uid=0)
            return original_stat(path, *args, **kwargs)
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {'pwd': types.SimpleNamespace(getpwuid=lambda uid: types.SimpleNamespace(pw_name='root'))}), \
             mock.patch.object(subprocess, 'Popen', FakePopen), mock.patch.object(builtins, 'open', fake_open), \
             mock.patch.object(os, 'stat', fake_stat), mock.patch.object(sys, 'argv', ['probe', '--expected-gpu-count=1']), \
             contextlib.redirect_stdout(output):
            exec(compile(server.REMOTE_PROBE, '<probe>', 'exec'), {})
        payload = json.loads(output.getvalue().splitlines()[-1])
        self.assertTrue(payload['ok'])
        self.assertEqual(len(payload['gpus'][0]['processes']), 1)
        self.assertEqual(payload['gpus'][0]['processes'][0]['user'], '')
        self.assertEqual(payload['gpus'][0]['processes'][0]['command_summary'], 'python train.py')
        self.assertNotIn('secret', output.getvalue())
        self.assertTrue(any('pid=,user:64=,lstart=,comm=' in command for command in queries))

    def test_nonfinite_persisted_user_seconds_are_rejected(self):
        self.assertEqual(server.parse_user_seconds('{"alice":NaN,"bob":Infinity,"carol":-Infinity,"dave":30}'), {'dave': 30})


class IdleViewerSchedulingTests(unittest.TestCase):
    def make_index(self, folder, clock=None):
        return ArtificialAnalysisIndex(Path(folder) / 'cache.json', Path(folder) / 'absent-key', clock=clock)

    def test_scheduled_refresh_occurs_without_request_or_maintenance(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = [100.0]
            index = self.make_index(folder, clock=lambda: clock[0])
            calls, delays = [], []
            class VirtualEvent:
                stopped = False
                def is_set(self): return self.stopped
                def wait(self, delay):
                    delays.append(delay)
                    clock[0] += delay
                    if clock[0] >= 161: self.stopped = True
                    return self.stopped
            index._scheduler_stop = VirtualEvent()
            index._next_attempt_at = 130.0
            def refresh():
                if clock[0] >= index._next_attempt_at:
                    calls.append(clock[0])
                    index._next_attempt_at = clock[0] + 3600
            with mock.patch.object(index, 'refresh_if_due', side_effect=refresh): index._run_scheduler()
            self.assertEqual(calls, [130.0])
            self.assertEqual(delays, [30.0, 60.0])

    def test_scheduler_start_is_idempotent_and_stop_wakes_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            index = self.make_index(folder)
            first_call = threading.Event()
            with mock.patch.object(index, 'refresh_if_due', side_effect=first_call.set):
                index._next_attempt_at = index._clock() + 3600
                index.start()
                self.assertTrue(first_call.wait(1))
                worker = index._scheduler_thread
                index.start()
                self.assertIs(index._scheduler_thread, worker)
                index.stop(timeout=1)
                self.assertFalse(worker.is_alive())

    def test_scheduler_failure_does_not_exit_or_leak_exception_detail(self):
        with tempfile.TemporaryDirectory() as folder:
            index = self.make_index(folder)
            event = mock.Mock()
            event.is_set.return_value = False
            event.wait.return_value = True
            index._scheduler_stop = event
            with mock.patch.object(index, 'refresh_if_due', side_effect=RuntimeError('secret')), \
                 mock.patch('gpu_watch.artificial_analysis.audit') as log:
                index._run_scheduler()
            event.wait.assert_called_once_with(60.0)
            log.assert_called_once_with('artificial_analysis_scheduler_failed')


if __name__ == '__main__': unittest.main()
