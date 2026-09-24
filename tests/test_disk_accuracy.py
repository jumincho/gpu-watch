import ast
import json
import os
import re
import tempfile
import time
import types
import unittest
from pathlib import Path
import server
from gpu_watch.processes import sanitize_disk_snapshot

class DiskAccuracyTests(unittest.TestCase):
    def collect(self,root,deny=False,inspect_error=False,partial_timeout=False):
        tree=ast.parse(server.REMOTE_PROBE)
        names={"collect_disk","user_roots","is_user_data_mount","is_capacity_filesystem","compact_error","to_int"}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        calls=[]
        def run(cmd,timeout=5):
            calls.append(cmd)
            if cmd[0]=="df":
                return 0,"Filesystem Type 1B-blocks Used Available Use% Mounted on\n/dev/test ext4 1000000000000 600000000000 400000000000 60% /\n",""
            if cmd[0]=="du":
                return (1 if deny else 0),f"4096\t{root}/alice\n4096\t{root}\n","du: Permission denied" if deny else ""
            if cmd[:2]==["docker","ps"]:
                return 0,json.dumps({"ID":"a"*64,"Names":"alice"}) + ("\n"+json.dumps({"ID":"b"*64,"Names":"bob"}) if partial_timeout else ""),""
            if cmd[:2]==["docker","info"]:return 0,"/var/lib/docker",""
            if cmd[:2]==["docker","inspect"]:
                if inspect_error or (partial_timeout and cmd[-1] == "b"*64):return 124,"","timeout"
                return 0,json.dumps({"Id":"a"*64,"Name":"/alice","SizeRw":1234567890,
                                     "Mounts":[]}),""
            if cmd[:3]==["docker","system","df"]:
                return 0,json.dumps({"Type":"Images","Size":"2GB"})+"\n"+json.dumps({"Type":"Build Cache","Size":"1GiB"}),""
            raise AssertionError(cmd)
        def lookup(user):
            if user=="alice":return types.SimpleNamespace(pw_name=user)
            raise KeyError(user)
        ns=dict(os=os,re=re,json=json,time=time,pwd=types.SimpleNamespace(getpwnam=lookup),
                run=run,du_timeout=30,disk_user_paths=[],include_docker=True,docker_usage_timeout=30,
                STANDARD_USER_ROOTS=[str(root)],SKIP_USER_DIRS=set(),SYSTEM_FS_TYPES={"tmpfs","overlay"})
        exec(compile(ast.Module(body=nodes,type_ignores=[]),"<disk>","exec"),ns)
        return ns["collect_disk"](),calls
    def test_permission_denied_is_lower_bound_and_docker_is_exact_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,"alice").mkdir()
            disk,calls=self.collect(root,deny=True)
        users={u["user"]:u for u in sanitize_disk_snapshot(disk)["users"]}
        self.assertEqual(users["alice"]["bytes"],4096+1234567890)
        self.assertFalse(users["alice"]["complete"])
        self.assertFalse(next(l for l in users["alice"]["locations"] if not l["path"].startswith("docker:"))["complete"])
        self.assertEqual(users["docker:images"]["bytes"],2000000000)
        self.assertEqual(users["docker:build-cache"]["bytes"],1024**3)
        inspect=next(c for c in calls if c[:2]==["docker","inspect"])
        self.assertIn("--size",inspect);self.assertIn("--format",inspect)
        self.assertNotIn(".Config",str(inspect));self.assertNotIn(".Env",str(inspect))
    def test_inspect_timeout_does_not_invent_zero_container_usage(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,"alice").mkdir()
            disk,_=self.collect(root,inspect_error=True)
        alice=next(u for u in disk["users"] if u["user"]=="alice")
        self.assertEqual(alice["bytes"],4096)
        self.assertTrue(any("docker inspect" in e for e in disk["errors"]))

    def test_one_timed_out_container_preserves_other_exact_results(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,"alice").mkdir()
            disk,_=self.collect(root,partial_timeout=True)
        alice=next(u for u in disk["users"] if u["user"]=="alice")
        self.assertEqual(alice["bytes"],4096+1234567890)
        self.assertTrue(any("docker inspect" in e for e in disk["errors"]))
