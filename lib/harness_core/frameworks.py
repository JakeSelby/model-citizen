"""Which framework a native spawn belongs to, read from a declared integration descriptor.

A framework that drives an agent session spawns subagents of its own, and the name it gives them
is whatever the client's model writes at the call. Confinement cannot be built on that name: the
routed instruction to run a constrained role through `harness role run` is a request in a prompt,
and a client that paraphrases the brief and names no role walks straight past a guard that only
reads `subagent_type` (#291).

So a framework declares itself instead. `policy/integrations/<id>.json` names the framework, the
version it is pinned to, how its spawns are recognised, which harness role each spawn maps to, and
the input roots a confined worker needs. The spawn hook classifies from that mapping, and a
`harness-role:` line in the brief stays what it always was: an optimisation that saves the
classifier the work, not the thing enforcement depends on.

Recognition is corroborated, because a false refusal is not a smaller mistake than a missed one:
a brief that is wrongly classified is work the session cannot get done, and the classifier has no
way to hear that it was wrong.

* **agents** — a spawn whose `subagent_type` is one of the framework's own layer names. Nothing but
  the framework puts that name there, so this alone is enough.
* **identifiers** — a literal only the framework's routed text carries, such as the path of one of
  its prompt files. Enough alone unless every mention of it is work on the file: a brief that
  edits it, or only summarises, counts, copies, explains or compares it, runs (`_adopted`).
  A subagent has one other reason to be handed a layer prompt file, and recognising that reason
  by its verbs leaked: most plain rewordings carried none of them (#739).
* **directed identifiers** — an identifier in a sentence that tells the subagent to follow or
  apply it: "read the instructions at <path> and follow them exactly". A client that writes the
  brief itself keeps the prompt file, because the subagent has to read it, and drops every
  sentence of the framework's own text (#739). A directive refuses even beside a summary or an
  edit of something else, which the default above would let run. It must govern
  the file: ahead of it and unbroken by a clause, or after it with a pronoun pointing back ("and
  follow them", "follow it" in a later sentence while the ones between still talk about the
  file). A negated directive, a directive aimed at
  something else, and a sentence that edits, updates or rewrites the file itself are not
  directives; "update your findings" edits something else and leaves the directive standing.
  The path has to end where the declared one does, so `<path>.bak` is another file, and a
  trailing "follow the instructions in <other>" names its own file. A qualified "the <words>
  instructions" points back only when its words are the path's own or generic ones.
* **phrases** — whole sentences of the framework's own prompt text, distinctive enough that
  quoting one is a coincidence and quoting `corroboration` of them is not. Single generic nouns
  are not phrases: "unified diff" and "list of findings" are what an ordinary fix-up brief says
  after a review, and refusing those was the first thing this classifier got wrong.

The `harness-role:` line is not a signal here: it is a standalone line the marker guard already
reads, and restating it as loose text would refuse prose that merely quotes it.

Input roots are declaration, never a signal: `_bmad/` names the framework but appears in any brief
about editing it. They travel into the refusal instead, so the sentence that refuses a spawn also
says which roots the isolated worker has to be given.

An optional `install` block carries the rest of what a framework costs the harness: where the
override templates live, where they are installed, which directory says the framework is present,
and where its skills declare the surface those templates rely on. `harness integration check|apply
<id>` reads it, so the CLI holds no framework name either, and the session hook's presence probe is
the block's `detect` path rather than a literal in the hook.

A descriptor that will not parse or will not validate is not enforcement that quietly stopped: the
loader keeps why it was ignored, and the spawn hook says so once per session.
"""
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTORS = ROOT / "policy" / "integrations"
SCHEMA_VERSION = 1
# A brief is normalised before it is searched, and only its head is searched: the framework's own
# instructions are at the top of every routed brief, and a hook runs on a tool call.
CLASSIFY_MAX = 20000
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*\Z")
# What a signal has to be before it is allowed to contribute to a refusal. A short or one-word
# string is something an unrelated brief says by accident, and the descriptor author does not
# find that out; the loader does, here.
MIN_IDENTIFIER = (12, 1)
MIN_PHRASE = (24, 4)
_CACHE = []


def normalise(text):
    """A brief reduced to what wording variance cannot hide: whitespace, case and length."""
    return " ".join(text.split()).casefold()[:CLASSIFY_MAX] if isinstance(text, str) else ""


def _too_slight(value, limits):
    characters, words = limits
    return len(value.strip()) < characters or len(value.split()) < words


def _signal_problems(where, spawn, roots):
    """What is wrong with one spawn entry's signals: shape, weight, and overlap with input roots."""
    found = []
    for field, limits in (("agents", (2, 1)), ("identifiers", MIN_IDENTIFIER), ("phrases", MIN_PHRASE)):
        values = spawn.get(field, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            found.append(where + "." + field + " must be a list of non-empty strings")
            continue
        for value in values:
            if _too_slight(value, limits):
                found.append(where + "." + field + ": `" + value + "` is too slight to identify a "
                             "spawn; " + field + " need at least " + str(limits[0]) + " characters "
                             "and " + str(limits[1]) + " word(s)")
            if field == "identifiers" and any(_covers(root, value) for root in roots):
                found.append(where + ".identifiers: `" + value + "` is an input root or a bare "
                             "directory under one, which any brief about the framework quotes")
    if not spawn.get("phrases"):
        found.append(where + " declares no phrases: agents and identifiers alone cannot tell the "
                     "framework's own work from a brief that quotes one of its paths")
    return found


def _covers(root, value):
    """Whether `value` is an input root, or a bare directory inside one rather than a file in it."""
    root, value = root.strip().strip("/").casefold(), value.strip().strip("/").casefold()
    if not root or not value:
        return False
    if value == root or not (value.startswith(root + "/") or root.startswith(value + "/")):
        return value == root
    tail = value[len(root) + 1:] if value.startswith(root + "/") else ""
    return "." not in tail.rsplit("/", 1)[-1]


def problems(data, role_check=None):
    """Everything wrong with one descriptor, as sentences. Empty means it is usable.

    Validation lives here rather than in the loader's exception handler so the shipped descriptors
    can be checked by a test, and so the loader can say which descriptor it ignored and why.

    `role_check` answers whether a role is one an isolated worker must run; it defaults to the
    lifecycle's own answer, because a descriptor mapping a spawn to a role the spawn guard would
    not constrain is a mapping that can never refuse anything.
    """
    found = []
    if not isinstance(data, dict):
        return ["descriptor is not an object"]
    if data.get("schema_version") != SCHEMA_VERSION:
        found.append("schema_version must be " + str(SCHEMA_VERSION))
    for field in ("id", "name"):
        value = data.get(field)
        if not (isinstance(value, str) and value.strip()):
            found.append(field + " must be a non-empty string")
    if not IDENTIFIER.match(str(data.get("id", ""))):
        found.append("id must be lowercase, starting with a letter")
    version = data.get("version")
    if not (isinstance(version, dict) and isinstance(version.get("pinned"), str) and version["pinned"].strip()):
        found.append("version.pinned must name the framework release this descriptor was read from")
    roots = data.get("input_roots", [])
    if not (isinstance(roots, list) and all(isinstance(r, str) and r.strip() for r in roots)):
        found.append("input_roots must be a list of paths")
        roots = []
    corroboration = data.get("corroboration", 2)
    if not (isinstance(corroboration, int) and not isinstance(corroboration, bool) and corroboration >= 2):
        found.append("corroboration must be an integer of at least 2")
    if "install" in data:
        found += _install_problems(data["install"])
    spawns = data.get("spawns")
    if not (isinstance(spawns, list) and spawns):
        return found + ["spawns must be a non-empty list"]
    if role_check is None:
        role_check = _constrained
    for index, spawn in enumerate(spawns):
        where = "spawns[" + str(index) + "]"
        if not isinstance(spawn, dict):
            found.append(where + " is not an object")
            continue
        for field in ("id", "role"):
            if not IDENTIFIER.match(str(spawn.get(field, ""))):
                found.append(where + "." + field + " must be a lowercase identifier")
        if IDENTIFIER.match(str(spawn.get("role", ""))) and not role_check(spawn["role"]):
            found.append(where + ".role `" + spawn["role"] + "` is not a role an isolated worker "
                         "must run, so this mapping could never refuse anything")
        found += _signal_problems(where, spawn, roots)
    return found


INSTALL_STRINGS = ("detect", "templates", "destination", "suffix", "skill_surface")
# The three that are resolved against a repository root or against this checkout. `apply` writes
# under one of them, so an absolute path or a `..` segment in a descriptor is a write outside the
# repository the operator named, and the descriptor is the wrong place to discover that.
INSTALL_PATHS = ("detect", "templates", "destination")


def _outside(value):
    """Whether a declared path would leave the root it is resolved against."""
    parts = PurePosixPath(value.strip()).parts
    return value.strip().startswith("/") or ".." in parts or (parts and parts[0].endswith(":"))


def _install_problems(install):
    """What is wrong with the optional install block `harness integration check|apply` reads."""
    if not isinstance(install, dict):
        return ["install must be an object"]
    found = []
    for field in INSTALL_STRINGS:
        value = install.get(field)
        if not (isinstance(value, str) and value.strip()):
            found.append("install." + field + " must be a non-empty string")
        elif field in INSTALL_PATHS and _outside(value):
            found.append("install." + field + " must be a relative path inside the repository, "
                         "with no `..` segment")
    roots = install.get("skill_roots")
    if not (isinstance(roots, list) and roots
            and all(isinstance(r, str) and r.strip() for r in roots)):
        found.append("install.skill_roots must be a non-empty list of paths")
    elif [r for r in roots if _outside(r)]:
        found.append("install.skill_roots must all be relative paths inside the repository, "
                     "with no `..` segment")
    return found


def installable(name, directory=None):
    """The descriptor `name`, or None. Used by the CLI, which also needs its install block."""
    for data in descriptors(directory):
        if data["id"] == name:
            return data
    return None


def _constrained(role):
    """Whether the spawn guard holds `role` to an isolated worker. False when it cannot be asked."""
    try:
        from . import lifecycle
        return lifecycle.constrained_role(role) is not None
    except Exception:
        return False


def _read(directory):
    """`(usable, ignored)` for one directory. `ignored` is `(path, reason)` for anything skipped."""
    try:
        paths = sorted(directory.glob("*.json"))
        stats = [p.stat() for p in paths]
    except OSError:
        return [], [], ()
    signature = tuple((str(p), st.st_mtime_ns, st.st_size) for p, st in zip(paths, stats))
    usable, ignored = [], []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            ignored.append((path.name, type(exc).__name__ + ": " + str(exc)))
            continue
        found = problems(data)
        if found:
            ignored.append((path.name, found[0] if len(found) == 1
                            else found[0] + " (and " + str(len(found) - 1) + " more)"))
        else:
            usable.append(data)
    return usable, ignored, signature


def _loaded(directory=None):
    """The cached `(usable, ignored)` for a directory, reread when any descriptor's bytes change."""
    directory = Path(directory) if directory else DESCRIPTORS
    usable, ignored, signature = _read(directory)
    if _CACHE and _CACHE[0][0] == signature:
        return _CACHE[0][1]
    del _CACHE[:]
    _CACHE.append((signature, (usable, ignored)))
    return usable, ignored


def descriptors(directory=None):
    """Every usable descriptor. A broken one is left out, and `ignored` says which and why."""
    return _loaded(directory)[0]


def ignored(directory=None):
    """`(file, reason)` for every descriptor the loader could not use."""
    return _loaded(directory)[1]


# What a sentence says, ahead of the file, to adopt it as the subagent's own instructions, in the
# base or -ing form an instruction takes. A third-person "follows" or "applied" describes, it does
# not direct. It governs the file only across a short gap with no clause break.
DIRECTIVE = re.compile(
    r"\b(?:follow(?:ing)?|apply(?:ing)?|obey(?:ing)?|adher(?:e|ing) to|comply(?:ing)? with"
    r"|abid(?:e|ing) by|carry(?:ing)? out|according to|as (?:instructed|directed|specified|"
    r"described|set out|laid out) (?:in|by)|per (?:the|those|these|its|that|this|their)\b"
    r"|your (?:\w+ ){0,2}(?:instructions|methodology|guidelines|checklist|rubric|procedure))\b")
LEAD_GAP = 8
CLAUSE_BREAK = re.compile(r"[,:()]|\b(?:and|then|but|or|while|after|before)\b")
# The directive after the file, later in its sentence or in the next one, which counts only when
# it points back at the file: "read <path> and follow it", not "read <path> and use it as a
# fixture".
DIRECTIVE_BACK = re.compile(
    r"\b(?:(?:follow(?:ing)?|apply(?:ing)?|obey(?:ing)?) (?:it|them|that file|this file"
    r"|(?:those|these|its|the) (?:[\w-]+ ){0,3}instructions)"
    r"|use (?:those|these|its|the) (?:[\w-]+ ){0,3}instructions"
    r"|use (?:it|them|that file|this file) (?:to|when|while|for|on|against)"
    r"|as (?:your |the )?(?:\w+ ){0,2}instructions"
    r"|do (?:exactly |just )?(?:what|whatever|as) (?:it|they|that file|this file)"
    r" (?:says|say|asks|instructs|tells you))\b")
# Work on the file rather than work under it, when the verb governs the file the way a directive
# does: "update <path>", or "update it" after it. "Update your findings" edits something else.
# Negated ("do not edit it") is still a directive.
EDIT_VERB = (r"(?:edit(?:s|ed|ing)?|modif(?:y|ies|ied|ying)|updat(?:e|es|ed|ing)"
             r"|rewrit(?:e|es|ing|ten)|rewrote|renam(?:e|es|ed|ing)|delet(?:e|es|ed|ing)"
             r"|remov(?:e|es|ed|ing)|reword(?:s|ed|ing)?|refactor(?:s|ed|ing)?|lint(?:s|ed|ing)?"
             r"|amend(?:s|ed|ing)?)")
EDIT = re.compile(r"\b" + EDIT_VERB + r"\b")
EDIT_BACK = re.compile(r"\b" + EDIT_VERB + r" (?:it|them|that file|this file)\b")
NEGATION = re.compile(r"\b(?:not|never|no|without|don't|do not)\s+(?:\w+\s+){0,2}\Z")
SENTENCE = re.compile(r"(?<=[.!?;])\s+|\s+(?:—|–|-{2})\s+")


def _unnegated(pattern, text):
    """The matches of `pattern` in `text` not cancelled by a "not", "never" or "without" before."""
    return [found for found in pattern.finditer(text) if not NEGATION.search(text[:found.start()])]


def _governs(pattern, before):
    """Whether a `pattern` verb ends close enough before the file, unbroken by a clause, to govern
    it."""
    for found in _unnegated(pattern, before):
        gap = before[found.end():]
        if len(gap.split()) <= LEAD_GAP and not CLAUSE_BREAK.search(gap):
            return True
    return False


# A directive in a later sentence still points back at the file while each sentence between keeps
# talking about it: "Read <path>. These instructions define the layer. Follow them precisely."
FOLLOW_REACH = 3
ANAPHOR = re.compile(
    r"\b(?:(?:these|those|its|the) (?:[\w-]+ ){0,3}instructions|(?:that|this|the) file)\b")
# An explanatory clause can start with a bare pronoun after an em dash: "read <path> — it
# contains the review instructions". `it` alone is too weak to carry the file forward, so require
# the pronoun to govern instruction-like guidance in the same clause.
PRONOUN_FILE = re.compile(
    r"\bit\s+(?:contains?|holds?|carries?|defines?|provides?|has|is|serves? as)\b")
GUIDANCE = re.compile(
    r"\b(?:instructions|methodology|guidelines|checklist|rubric|procedure|directions|rules|criteria"
    r"|requirements|prompt)\b")
GUIDANCE_NEGATION = re.compile(r"\b(?:no|not|never|without)\b")
# A trailing "the instructions" followed by where they live names its own file, not this one.
OWN_TARGET = re.compile(r"\s+(?:in|at|from|of|under|inside)\b")
# The declared path ends where a longer file name would go on: `<path>.bak` is another file.
PATH_END = r"(?![\w/-]|\.\w)"
# And starts at a path boundary: `my<path>` is another file, `x/<path>` is the same one deeper.
PATH_START = r"(?<![\w.-])"
# A file anaphor bound to a file it names: "the file docs/other.md", "the instructions in x.md".
NAMED_FILE = re.compile(r"\s+(?:(?:in|at|from|of|under|inside|named|called)\s+)?[`'\"]?"
                        r"([\w.~/-]*[\w-](?:/[\w.-]+|\.[a-z]\w*))")
# A bare read of the file, and a look at it that ends the brief: "read <path>. summarise it in
# three bullets." The look may not go on into another clause or point back at the file again.
READ_ONLY = re.compile(r"(?:(?:first|now|please),? )?(?:read|open) [\w.~/-]*")
POINTER = re.compile(r"\b(?:it|its|them|they|their|that|those|these|accordingly)\b")


DETERMINERS = ("those", "these", "its", "the", "your", "as")
# Words a qualified "the <words> instructions" may carry and still mean the declared file, beside
# the words of its own path: "those edge-case-hunter review instructions" does, "the house-style
# instructions" and "the commit instructions" do not.
GENERIC_QUALIFIERS = frozenset(("review", "layer", "layer's", "prompt", "prompt's", "file", "file's",
                                "own", "exact", "full", "detailed", "same", "above"))


def _path_words(value):
    """The words of a declared path a qualifier may repeat: its segments and their parts."""
    value = normalise(value)
    return set(re.split(r"[/.]+", value)) | set(re.split(r"[/._-]+", value))


def _bound(said, value):
    """Whether a matched "<determiner> <words> instructions" is about the file `value`. A phrase
    that is not a qualified "instructions" ("follow it", "that file") always is."""
    words = said.split()
    if not words or words[-1] != "instructions":
        return True
    allowed = _path_words(value) | GENERIC_QUALIFIERS
    for word in reversed(words[:-1]):
        if word in DETERMINERS:
            break
        if word not in allowed:
            return False
    return True


def _points_back(after, value):
    """Whether `after` holds an unnegated directive aimed back at the file `value` before it."""
    return any(_bound(found.group(0), value) and not _names_its_own(found, after)
               for found in _unnegated(DIRECTIVE_BACK, after))


def _refers_back(sentence, value):
    """Whether a sentence between the file and a later directive still talks about the file. An
    anaphor that names its own file ("the file docs/other.md") talks about that one instead."""
    return (any(_bound(found.group(0), value) and not _names_other(found, sentence, value)
                for found in ANAPHOR.finditer(sentence))
            or _pronoun_guidance(sentence))


def _names_other(found, sentence, value):
    """Whether the anaphor `found` is followed by a file name other than `value`."""
    named = NAMED_FILE.match(sentence, found.end())
    return bool(named) and not _path(value).search(named.group(1))


def _pronoun_guidance(text):
    """Whether a bare `it` still describes the named file as guidance in this clause."""
    for subject in PRONOUN_FILE.finditer(text):
        for guidance in GUIDANCE.finditer(text, subject.end()):
            gap = text[subject.end():guidance.start()]
            if not CLAUSE_BREAK.search(gap) and not GUIDANCE_NEGATION.search(gap):
                return True
    return False


def _names_its_own(found, after):
    """Whether a trailing "follow the instructions" says where they live, so they are not ours."""
    said = found.group(0)
    return said.endswith("instructions") and not said.startswith("as ") and bool(
        OWN_TARGET.match(after, found.end()))


def _path(value):
    return re.compile(PATH_START + re.escape(normalise(value)) + PATH_END)


def _directed(value, text):
    """Whether `text` tells the subagent to follow or apply the file named `value`."""
    path = _path(value)
    sentences = SENTENCE.split(text)
    for index, sentence in enumerate(sentences):
        if not path.search(sentence):
            continue
        parts = path.split(sentence)
        pairs = [(parts[at - 1], parts[at]) for at in range(1, len(parts))]
        if any(_governs(EDIT, before) or _unnegated(EDIT_BACK, after) for before, after in pairs):
            # An edit covers its own clause, not a later one that takes the file on: "edit <path>,
            # then follow it" adopts, "update <path> so reviewers follow it" does not.
            if any(_points_back(_after_clause(after), value) for _, after in pairs):
                return True
            continue
        if any(_governs(DIRECTIVE, before) or _points_back(after, value) for before, after in pairs):
            return True
        for following in sentences[index + 1:index + 1 + FOLLOW_REACH]:
            if _points_back(following, value) and not _unnegated(EDIT_BACK, following):
                return True
            if not _refers_back(following, value):
                break
    return False


def _after_clause(text):
    """What follows the first clause break in `text`, or nothing when there is none."""
    found = CLAUSE_BREAK.search(text)
    return text[found.end():] if found else ""


# Work that only looks at the file rather than working under it: summarise, count, copy, explain or
# compare it, or ask about it. Like an edit, the verb must govern the file, ahead of it or pointing
# back at it, so "summarise your findings" beside the file does not count.
META_VERB = (r"(?:summari[sz](?:e|ing)|count(?:ing)?|cop(?:y|ying)|explain(?:ing)?"
             r"|compar(?:e|ing)|tell me|check (?:whether|if))")
META = re.compile(r"\b" + META_VERB + r"\b")
META_BACK = re.compile(r"\b" + META_VERB + r" (?:\w+ ){0,2}(?:it|them|its|that file|this file)\b")


def _works_on(before, after):
    """Whether one mention of the file is work on it, an edit or a look, rather than under it."""
    return bool(_governs(EDIT, before) or _unnegated(EDIT_BACK, after)
                or _governs(META, before) or _unnegated(META_BACK, after))


def _adopted(value, text):
    """Whether any mention of the file `value` is something other than work on the file itself.

    A declared prompt file has one use besides being edited or looked at: a subagent reads it to
    take on the layer. So the file refuses by default, and only a brief whose every mention edits,
    summarises, counts, copies, explains or compares it runs. A verb list for adoption leaked:
    "use the instructions in <path>", "do what it says" and "review it accordingly" all ran (#739).
    """
    path = _path(value)
    sentences = SENTENCE.split(text)
    for index, sentence in enumerate(sentences):
        parts = path.split(sentence)
        for at in range(1, len(parts)):
            if not _works_on(parts[at - 1], parts[at]) and not _read_then_looked_at(
                    parts[at - 1], parts[at], sentences[index + 1:]):
                return True
    return False


def _read_then_looked_at(before, after, rest):
    """Whether a bare "read <path>." is followed by one last sentence that only looks at it."""
    if not READ_ONLY.fullmatch(before) or after.strip(" .!") or len(rest) != 1:
        return False
    look = re.match(r"(?:then,? )?" + META_VERB + r" (?:it|them|that file|this file)\b", rest[0])
    tail = rest[0][look.end():] if look else ""
    return bool(look) and not CLAUSE_BREAK.search(tail) and not POINTER.search(tail)


def _score(spawn, text, agent):
    """`(agents, directed, identifiers, phrases, adopted)` this spawn entry matched."""
    agents = 1 if agent and agent in [a.casefold() for a in spawn.get("agents", [])] else 0
    named = [value for value in spawn.get("identifiers", []) if _path(value).search(text)]
    directed = sum(1 for value in named if _directed(value, text))
    phrases = sum(1 for value in spawn.get("phrases", []) if normalise(value) in text)
    adopted = sum(1 for value in named if _adopted(value, text))
    return agents, directed, len(named), phrases, adopted


def _recognised(score, corroboration):
    """Whether this much evidence refuses a spawn. The rule, in one place, for the one caller."""
    agents, directed, identifiers, phrases, adopted = score
    if agents or directed or adopted:
        return True
    if identifiers and phrases:
        return True
    return phrases >= corroboration


def classify(prompt, subagent_type=None, directory=None, accept=None):
    """The framework spawn this call is, or None.

    `accept` filters the roles a match may map to, so a spawn the guard would go on to allow
    anyway cannot outscore one it would refuse. The best-scoring surviving entry wins, which is
    what classifies a brief carrying both a framework's general review wording and its specific
    audit wording as the audit.
    """
    text = normalise(prompt)
    agent = subagent_type.strip().casefold() if isinstance(subagent_type, str) else ""
    best = None
    for data in descriptors(directory):
        for spawn in data["spawns"]:
            if accept is not None and not accept(spawn["role"]):
                continue
            score = _score(spawn, text, agent)
            if not _recognised(score, data.get("corroboration", 2)):
                continue
            if best is None or score > best[0]:
                best = (score, {"framework": data["id"], "framework_name": data["name"],
                                "spawn": spawn["id"], "role": spawn["role"],
                                "version": data["version"]["pinned"],
                                "input_roots": list(data.get("input_roots", []))})
    return best[1] if best else None


def origin(match):
    """The sentences a refusal opens with: what was recognised, and what the worker will need."""
    said = ("This spawn carries the " + match["framework_name"] + " " + match["version"] + " `"
            + match["spawn"] + "` work, which this installation runs as the constrained `"
            + match["role"] + "` role whatever the spawn called itself.")
    roots = match.get("input_roots") or []
    if roots:
        said += (" The isolated worker needs the framework's input roots as read roots: "
                 + ", ".join(roots) + ".")
    return said
