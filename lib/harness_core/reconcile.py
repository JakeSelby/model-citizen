"""Owned-file reconciliation with durable intent and conflict-preserving rollback."""
import contextlib
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "tomlkit-0.15.1-py3-none-any.whl"))
import tomlkit


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".harness-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


HOLDER_FILE = "sync.lock.holder"


def lock_holder(directory):
    """The operation a caller named when it took `directory`'s lock, or None when it named none."""
    try:
        record = json.loads((Path(directory) / HOLDER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("holder"), str):
        return None
    return "%s (pid %s, since %s)" % (record["holder"], record.get("pid"), record.get("since"))


@contextlib.contextmanager
def lock(directory, holder=None):
    """Refuse at once, never wait, while another operation holds `directory`'s lock.

    A caller that passes `holder` is named in the refusal a second caller sees. The record is
    written only after the lock is taken and removed before it is released, so it never names a
    process that no longer holds the lock; a stale one a crash left is cleared by the next holder.
    """
    import datetime
    import fcntl
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    named = directory / HOLDER_FILE
    with open(directory / "sync.lock", "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            current = lock_holder(directory)
            raise ValueError("another harness configuration operation is running"
                             + (": " + current if current else ""))
        try:
            if holder:
                since = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
                atomic_text(named, json.dumps({"holder": holder, "pid": os.getpid(),
                                               "since": since.isoformat()}) + "\n")
            elif named.exists():
                named.unlink()
            yield
        finally:
            try:
                if holder and named.exists():
                    named.unlink()
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def update_toml(text, wanted):
    """Apply owned top-level keys; a `None` value means the harness no longer wants that key."""
    document = tomlkit.parse(text)
    for key, value in wanted.items():
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    return tomlkit.dumps(document)


def lookup(document, keys):
    node = document
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return {"present": False, "value": None}
        node = node[key]
    return {"present": True, "value": deepcopy(node)}


def assign(document, keys, item):
    node = document
    for key in keys[:-1]:
        if key not in node:
            if not item["present"]:
                return
            node[key] = {}
        if not isinstance(node[key], dict):
            raise ValueError("configuration parent is not an object: " + key)
        node = node[key]
    if item["present"]:
        node[keys[-1]] = item["value"]
    else:
        node.pop(keys[-1], None)


# Hook scripts earlier releases registered by name, before commands carried a marker.
HARNESS_HOOK_BASENAMES = [
    "validate-plan-card.py", "allow-readonly-bash.py", "harness-session.py",
    "neutralize-tool-output.py",
]


def _commands(entry):
    """The command strings of a hook entry; anything malformed has none, so it is the user's."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return []
    return [str(hook.get("command", "")) for hook in hooks if isinstance(hook, dict)]


def hook_marker(entry):
    for command in _commands(entry):
        match = re.search(r"# harness:([a-z0-9-]+)", command)
        if match:
            return match.group(1)
    return None


def is_harness_hook_entry(entry):
    """An entry the harness registered: it carries a `# harness:` marker, or runs one of the
    legacy scripts by name as a whole path component, so `my-harness-session.py` is not one."""
    if hook_marker(entry):
        return True
    return any(re.search(r"(?:^|[\s/'\"])" + re.escape(name) + r"(?:$|[\s'\"])", command)
               for command in _commands(entry) for name in HARNESS_HOOK_BASENAMES)


# A hook event's list is shared: the harness registers its entries beside the user's own.
# A record marked with one of these owns only the entries its predicate recognizes, so a
# user's hook is neither a conflict at sync, nor drift, nor removed at uninstall.
HOOK_ENTRIES = "harness-hooks"
ENTRY_OWNERS = {HOOK_ENTRIES: is_harness_hook_entry}


def entries_of(keys, record):
    """The entry owner of a record. Journals written before owners were recorded name none, and
    their hook event lists were shared all the same, so the key's shape decides for them."""
    if record.get("entries"):
        return record["entries"]
    return HOOK_ENTRIES if len(keys) == 2 and keys[0] == "hooks" else None


def owned_part(item, entries):
    """The part of a looked-up value a record owns: all of it, or its own entries in a list.
    For a shared list an absent value owns nothing, the same as a list of only user entries."""
    owner = ENTRY_OWNERS.get(entries)
    if owner is None:
        return item
    if not item["present"]:
        return {"present": True, "value": []}
    if not isinstance(item["value"], list):
        return item
    return {"present": True, "value": [entry for entry in item["value"] if owner(entry)]}


def user_changed(live, record, entries=None):
    """True when what the record owns is neither what was applied nor what was being applied."""
    mine = owned_part(live, entries)
    return mine != owned_part(record["applied"], entries) and (
        "pending_from" not in record or mine != owned_part(record["pending_from"], entries))


def restored(live, record, entries=None):
    """What uninstall leaves: the prior value, or the user's own entries of a shared list."""
    owner = ENTRY_OWNERS.get(entries)
    if owner is None or not live["present"] or not isinstance(live["value"], list):
        return record["prior"]
    rest = [entry for entry in live["value"] if not owner(entry)]
    if rest or record["prior"]["present"]:
        return {"present": True, "value": rest}
    return {"present": False, "value": None}


class Store:
    def __init__(self, directory, dry=False):
        self.path = directory / "ownership.json"
        self.dry = dry
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"schema_version": 1, "files": {}}
        self.conflicts = []

    def save(self):
        if not self.dry:
            atomic_text(self.path, json.dumps(self.data, indent=2) + "\n")

    def _write(self, path, text, record):
        # Persist intent first. Recovery accepts either the prior or intended content.
        self.data["files"][str(path)] = record
        self.save()
        if not self.dry:
            atomic_text(path, text)
            record.pop("pending_from", None)
            for owned in record.get("keys", {}).values():
                owned.pop("pending_from", None)
            self.save()

    def generated(self, path, text, adopt=False, over_link=False):
        """Write a file the harness owns. `over_link` is a link the caller has already claimed.

        A dry run reports what a real one would do, and a real one unlinks before writing, so a
        claimed link is treated as an absent file rather than as content to compare against: the
        alternative is a dry run that reports nothing wherever the previous release left a link.
        """
        path = Path(path)
        if path.is_symlink() and not over_link:
            self.conflicts.append(str(path) + ": symlink is not a generated-file target")
            return
        current = None if path.is_symlink() else (path.read_text() if path.exists() else None)
        record = self.data["files"].get(str(path))
        if record and current != record["applied"] and ("pending_from" not in record or current != record["pending_from"]):
            self.conflicts.append(str(path) + ": generated content changed; preserve and reconcile it first")
            return
        if record is None and current is not None and not adopt:
            self.conflicts.append(str(path) + ": unmanaged file; adopt explicitly before replacing it")
            return
        prior = record["prior"] if record else current
        next_record = {"kind": "generated", "prior": prior, "applied": text, "pending_from": current}
        if current != text or record is None:
            self._write(path, text, next_record)

    def toml(self, path, wanted):
        path = Path(path)
        if path.is_symlink():
            self.conflicts.append(str(path) + ": configuration symlink is not managed")
            return
        text = path.read_text() if path.exists() else ""
        document = tomlkit.parse(text)
        record = self.data["files"].get(str(path), {"kind": "toml", "keys": {}, "created": not path.exists()})
        for key, value in wanted.items():
            current = document[key].unwrap() if key in document else None
            old = record["keys"].get(key)
            if old and current != old["applied"] and ("pending_from" not in old or current != old["pending_from"]):
                self.conflicts.append(str(path) + ": owned key changed: " + key)
                continue
            if value is None:
                # A key the harness wrote and no longer wants — a renamed setting, say. Only one
                # it owns: a key of the same name the user set themselves has no record here and
                # is left exactly as it is.
                if old is None:
                    continue
                if old["prior"]["present"]:
                    document[key] = old["prior"]["value"]
                elif key in document:
                    del document[key]
                del record["keys"][key]
                continue
            prior = old["prior"] if old else {"present": key in document, "value": current}
            record["keys"][key] = {"prior": prior, "applied": value, "pending_from": current}
            document[key] = value
        rendered = tomlkit.dumps(document)
        if rendered != text or str(path) not in self.data["files"]:
            self._write(path, rendered, record)

    def json(self, path, desired, owned_paths, hook_lists=()):
        """Apply owned fields. An owned path also in `hook_lists` holds a hook event's list, of
        which the harness owns only its own entries; `desired` must already carry the user's."""
        path = Path(path)
        if path.is_symlink():
            self.conflicts.append(str(path) + ": configuration symlink is not managed")
            return
        current = json.loads(path.read_text()) if path.exists() else {}
        document = deepcopy(current)
        record = self.data["files"].get(str(path), {"kind": "json", "keys": {}, "created": not path.exists()})
        shared = {json.dumps(keys) for keys in hook_lists}
        for keys in owned_paths:
            name = json.dumps(keys)
            entries = HOOK_ENTRIES if name in shared else None
            live, wanted = lookup(current, keys), lookup(desired, keys)
            old = record["keys"].get(name)
            if old and user_changed(live, old, entries):
                self.conflicts.append(str(path) + ": owned field changed: " + ".".join(keys))
                continue
            record["keys"][name] = {"prior": old["prior"] if old else live,
                                    "applied": wanted, "pending_from": live}
            if entries:
                record["keys"][name]["entries"] = entries
            assign(document, keys, wanted)
        if document != current or str(path) not in self.data["files"]:
            self._write(path, json.dumps(document, indent=2) + "\n", record)

    def retire(self, path):
        """Undo one generated file the harness no longer supplies; True when it is gone.

        `uninstall`'s rule for a single path, because a role that is deleted or renamed would
        otherwise leave its definition on every machine forever. Content the user changed is
        preserved and reported, and a file that had a prior life is returned to it.
        """
        path = Path(path)
        record = self.data["files"].get(str(path))
        if record is None or record.get("kind") != "generated":
            return False
        if path.is_symlink():
            self.conflicts.append(str(path) + ": redirected path preserved")
            return False
        current = path.read_text() if path.exists() else None
        if current is not None and current != record["applied"] and (
                "pending_from" not in record or current != record["pending_from"]):
            self.conflicts.append(str(path) + ": user changes preserved")
            return False
        if not self.dry:
            if record["prior"] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_text(path, record["prior"])
        del self.data["files"][str(path)]
        self.save()
        return True

    def drift(self):
        findings = []
        for name, record in self.data["files"].items():
            path = Path(name)
            if not path.is_file() or path.is_symlink():
                findings.append("missing or redirected managed file: " + name)
                continue
            current = path.read_text()
            if record["kind"] == "generated":
                if current != record["applied"]:
                    findings.append("modified generated file: " + name)
            elif record["kind"] == "json":
                try:
                    doc = json.loads(current)
                    for key, value in record["keys"].items():
                        keys = json.loads(key)
                        entries = entries_of(keys, value)
                        if owned_part(lookup(doc, keys), entries) != owned_part(value["applied"], entries):
                            findings.append("modified owned field: " + name + ":" + key)
                except Exception:
                    findings.append("invalid managed JSON: " + name)
            else:
                try:
                    doc = tomlkit.parse(current)
                    for key, value in record["keys"].items():
                        if key not in doc or doc[key].unwrap() != value["applied"]:
                            findings.append("modified owned key: " + name + ":" + key)
                except Exception:
                    findings.append("invalid managed TOML: " + name)
        return findings

    def uninstall(self):
        for name, record in list(self.data["files"].items()):
            path = Path(name)
            if path.is_symlink():
                self.conflicts.append(name + ": redirected path preserved")
                continue
            current = path.read_text() if path.exists() else None
            if record["kind"] == "generated":
                if current != record["applied"] and ("pending_from" not in record or current != record["pending_from"]):
                    self.conflicts.append(name + ": user changes preserved")
                    continue
                if not self.dry:
                    if record["prior"] is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_text(path, record["prior"])
            elif record["kind"] == "json":
                try:
                    doc = json.loads(current or "{}")
                except Exception:
                    self.conflicts.append(name + ": invalid JSON preserved")
                    continue
                remaining = {}
                for key, values in record["keys"].items():
                    keys = json.loads(key)
                    live = lookup(doc, keys)
                    entries = entries_of(keys, values)
                    if user_changed(live, values, entries):
                        self.conflicts.append(name + ": user changes preserved for " + key)
                        remaining[key] = values
                        continue
                    assign(doc, keys, restored(live, values, entries))
                if not self.dry:
                    atomic_text(path, json.dumps(doc, indent=2) + "\n")
                if remaining:
                    record["keys"] = remaining
                    self.save()
                    continue
            else:
                try:
                    doc = tomlkit.parse(current or "")
                except Exception:
                    self.conflicts.append(name + ": invalid TOML preserved")
                    continue
                remaining = {}
                for key, values in record["keys"].items():
                    live = doc[key].unwrap() if key in doc else None
                    if live != values["applied"] and ("pending_from" not in values or live != values["pending_from"]):
                        self.conflicts.append(name + ": user changes preserved for " + key)
                        remaining[key] = values
                        continue
                    prior = values["prior"]
                    if prior["present"]:
                        doc[key] = prior["value"]
                    elif key in doc:
                        del doc[key]
                if not self.dry:
                    if record["created"] and not doc and not tomlkit.dumps(doc).strip():
                        path.unlink(missing_ok=True)
                    else:
                        atomic_text(path, tomlkit.dumps(doc))
                if remaining:
                    record["keys"] = remaining
                    self.save()
                    continue
            del self.data["files"][name]
            self.save()
