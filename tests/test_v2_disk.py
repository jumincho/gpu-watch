import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path

import server


@unittest.skipUnless(os.name != "nt" and shutil.which("du"), "GNU du is required")
class RelocatedDockerStorageTests(unittest.TestCase):
    def collect(self, root, docker_root, *, partial=False, lookup_failure=False, explicit=None):
        names = {"collect_disk", "user_roots", "is_user_data_mount", "is_capacity_filesystem", "compact_error", "to_int"}
        nodes = [node for node in ast.parse(server.REMOTE_PROBE).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        calls = []

        def run(command, timeout=5):
            calls.append(command)
            if command[0] == "df":
                return 0, "Filesystem Type 1B-blocks Used Available Use% Mounted on\n/dev/test ext4 100000000000 60000000000 40000000000 60% /\n", ""
            if command[0] == "du":
                process = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
                return (1 if partial else process.returncode), process.stdout, "Permission denied" if partial else process.stderr
            if command[:2] == ["docker", "info"]:
                return (1, "", "failure") if lookup_failure else (0, str(docker_root), "")
            if command[:2] == ["docker", "ps"]:
                return 0, json.dumps({"ID": "a" * 64, "Names": "alice"}), ""
            if command[:2] == ["docker", "inspect"]:
                return 0, json.dumps({"Id": "a" * 64, "SizeRw": 1234567890, "Mounts": []}), ""
            if command[:3] == ["docker", "system", "df"]:
                return 0, json.dumps({"Type": "Images", "Size": "2GB"}), ""
            raise AssertionError(command)

        namespace = dict(os=os, re=re, json=json, time=time,
            pwd=types.SimpleNamespace(getpwnam=lambda user: types.SimpleNamespace(pw_name=user)),
            run=run, du_timeout=30, disk_user_paths=explicit or [], include_docker=True,
            docker_usage_timeout=30, STANDARD_USER_ROOTS=[str(root)], SKIP_USER_DIRS=set(),
            SYSTEM_FS_TYPES={"tmpfs", "overlay"})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<disk-test>", "exec"), namespace)
        return namespace["collect_disk"](), calls

    def test_relocated_storage_is_excluded_and_docker_bytes_survive_partial_du(self):
        for partial in [False, True]:
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                docker_root = root / "docker"
                docker_root.mkdir()
                (docker_root / "private-layer").write_bytes(b"x" * 65536)
                (root / "alice").mkdir()
                disk, calls = self.collect(root, docker_root, partial=partial)
                users = {row["user"]: row for row in disk["users"]}
                self.assertNotIn("docker", users)
                self.assertEqual(users["alice"]["bytes"], (root / "alice").stat().st_blocks * 512 + 1234567890)
                self.assertEqual(users["alice"]["complete"], not partial)
                self.assertEqual(users["docker:images"]["bytes"], 2000000000)
                self.assertEqual(sum(command[:2] == ["docker", "info"] for command in calls), 1)
                self.assertTrue(any(command[:2] == ["docker", "inspect"] for command in calls))
                ps = next(command for command in calls if command[:2] == ["docker", "ps"])
                self.assertEqual(ps[-1], '{"ID":{{json .ID}},"Names":{{json .Names}}}')
                self.assertNotIn("{{json .}}", ps)

    def test_literal_glob_characters_and_symlink_root_do_not_hide_siblings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "storage"
            root.mkdir()
            docker_root = root / ("docker[1]*?" + chr(92) + "path")
            docker_root.mkdir()
            (docker_root / "layer").write_bytes(b"x" * 65536)
            sibling = root / "docker1AB"
            sibling.mkdir()
            (sibling / "keep").write_bytes(b"y" * 32768)
            alias = Path(temporary) / "alias"
            alias.symlink_to(root, target_is_directory=True)
            disk, calls = self.collect(alias, docker_root)
            users = {row["user"]: row for row in disk["users"]}
            self.assertNotIn(docker_root.name, users)
            self.assertIn(sibling.name, users)
            self.assertGreaterEqual(users[sibling.name]["bytes"], 32768)
            exclusion = next(item for command in calls if command[0] == "du" for item in command if item.startswith("--exclude="))
            self.assertIn(str(alias), exclusion)

    def test_nested_storage_and_explicit_paths_do_not_double_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "users"
            root.mkdir()
            (root / "alice").mkdir()
            other = base / "other"
            docker_root = other / "docker"
            docker_root.mkdir(parents=True)
            (docker_root / "layer").write_bytes(b"x" * 65536)
            (other / "keep").write_bytes(b"y" * 8192)
            disk, calls = self.collect(root, docker_root, explicit=[{"user": "other", "path": str(other)}])
            users = {row["user"]: row for row in disk["users"]}
            expected = other.stat().st_blocks * 512 + (other / "keep").stat().st_blocks * 512
            self.assertEqual(users["other"]["bytes"], expected)
            self.assertTrue(any(command[0] == "du" and "-s" in command and any(x.startswith("--exclude=") for x in command) for command in calls))

    def test_failed_root_lookup_keeps_directory_data_and_reports_omission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker_root = root / "docker"
            docker_root.mkdir()
            (docker_root / "layer").write_bytes(b"x" * 65536)
            disk, calls = self.collect(root, docker_root, lookup_failure=True)
            self.assertIn("docker", {row["user"] for row in disk["users"]})
            self.assertTrue(any("storage root lookup failed" in error for error in disk["errors"]))
            self.assertFalse(any(command[:2] == ["docker", "inspect"] for command in calls))

    def test_nested_explicit_home_is_assigned_without_parent_double_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            home = root / "tmp" / "carol"
            home.mkdir(parents=True)
            (home / "payload").write_bytes(b"x" * 65536)
            (root / "tmp" / "other").write_bytes(b"y" * 32768)
            alias = Path(temporary) / "home-alias"
            alias.symlink_to(home, target_is_directory=True)
            disk, calls = self.collect(root, Path(temporary) / "docker", explicit=[
                {"user": "carol", "path": str(alias)},
                {"user": "duplicate", "path": str(home)},
            ])
            users = {row["user"]: row for row in disk["users"]}
            home_bytes = home.stat().st_blocks * 512 + (home / "payload").stat().st_blocks * 512
            other_bytes = (root / "tmp").stat().st_blocks * 512 + (root / "tmp" / "other").stat().st_blocks * 512
            self.assertEqual(users["carol"]["bytes"], home_bytes)
            self.assertEqual(users["tmp"]["bytes"], other_bytes)
            self.assertNotIn("duplicate", users)
            self.assertEqual(sum(command[0] == "du" and "-s" in command for command in calls), 1)

    def test_nested_explicit_homes_partition_each_other_and_keep_docker_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            parent = root / "parent"
            child = parent / "child"
            docker_root = child / "docker"
            docker_root.mkdir(parents=True)
            (parent / "parent-file").write_bytes(b"x" * 8192)
            (child / "child-file").write_bytes(b"y" * 16384)
            (docker_root / "layer").write_bytes(b"z" * 65536)
            disk, _ = self.collect(root, docker_root, explicit=[
                {"user": "parent-owner", "path": str(parent)},
                {"user": "child-owner", "path": str(child)},
            ])
            users = {row["user"]: row for row in disk["users"]}
            self.assertNotIn("parent", users)
            self.assertEqual(users["parent-owner"]["bytes"], parent.stat().st_blocks * 512 + (parent / "parent-file").stat().st_blocks * 512)
            self.assertEqual(users["child-owner"]["bytes"], child.stat().st_blocks * 512 + (child / "child-file").stat().st_blocks * 512)
