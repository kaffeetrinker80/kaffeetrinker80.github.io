#!/usr/bin/env python3
"""
Auto-Changelog für kaffeetrinker80.github.io

Die Tools liegen in eigenen Repos (theocratic, worktools, private, nova).
Dieses Skript holt sich diese Repos, schaut nach, was sich seit dem letzten
Lauf geändert hat, und trägt das in changelog.json ein.

Merkt sich pro Repo den zuletzt verarbeiteten Commit in
.github/changelog-state.json.
"""
import html, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OWNER = "kaffeetrinker80"
REPOS = {  # Repo -> Kategorie (Farbpunkt im Changelog)
    "theocratic": "theocratic",
    "worktools": "work",
    "private": "projects",
    "nova": "nova",
}
CHANGELOG = "changelog.json"
STATE = ".github/changelog-state.json"
MAX_ENTRIES = 80
FIRST_RUN_DAYS = int(os.environ.get("FIRST_RUN_DAYS", "3"))
TZ = ZoneInfo("Europe/Berlin")

CODE_EXT = (".html", ".htm", ".js", ".css", ".json", ".md")
SKIP_FOLDERS = {".github", "assets", "shared", "common", "img", "images", "icons"}
GENERIC_MSG = re.compile(
    r"^(add files via upload|update\b|create\b|delete\b|rename\b|merge\b|upload\b|initial commit|chore\b)", re.I)
VERSION_PATTERNS = [
    re.compile(r"APP_VERSION\s*=\s*['\"]v?(\d+\.\d+(?:\.\d+)?)['\"]"),
    re.compile(r"\bversion\s*[:=]\s*['\"]v?(\d+\.\d+\.\d+)['\"]", re.I),
    re.compile(r"\bV(?:ersion)?\s?(\d+\.\d+\.\d+)\b"),
]
FILENAME_VERSION = re.compile(r"-v\.?(\d+\.\d+\.\d+)\.html?$", re.I)


def git(repo_dir, *args, check=True):
    r = subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout if r.returncode == 0 else None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def tool_names():
    """Anzeigenamen aus der publicTools-Liste der Landing-Page."""
    names = {}
    try:
        src = open("index.html", encoding="utf-8").read()
    except OSError:
        return names
    for m in re.finditer(r'name:\s*"([^"]+)"[^}]*?path:\s*"/([^"]+?)/?(?:index\.html)?"', src):
        names[m.group(2).strip("/")] = m.group(1)
    return names


def show(repo_dir, rev, path):
    return git(repo_dir, "show", f"{rev}:{path}", check=False) or ""


def find_version(repo_dir, rev, files):
    for f in sorted(files, key=lambda p: (not p.endswith("index.html"), p)):
        m = FILENAME_VERSION.search(f)
        if m:
            return m.group(1)
        if f.lower().endswith((".html", ".htm", ".js")):
            text = show(repo_dir, rev, f)
            for pat in VERSION_PATTERNS:
                m = pat.search(text)
                if m:
                    return m.group(1)
    return None


def folder_exists(repo_dir, rev, folder):
    return bool(rev) and subprocess.run(
        ["git", "-C", repo_dir, "cat-file", "-e", f"{rev}:{folder}"], capture_output=True).returncode == 0


def title_of(repo_dir, rev, folder):
    m = re.search(r"<title>([^<]+)</title>", show(repo_dir, rev, f"{folder}/index.html"), re.I)
    return m.group(1).strip() if m else folder.replace("-", " ").title()


def process_repo(repo, category, last_sha, names, workdir):
    url = f"https://github.com/{OWNER}/{repo}.git"
    d = os.path.join(workdir, repo)
    r = subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", url, d], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠ {repo}: Klonen fehlgeschlagen – übersprungen ({r.stderr.strip()[:120]})")
        return [], last_sha
    head = git(d, "rev-parse", "HEAD").strip()

    known = bool(last_sha) and subprocess.run(
        ["git", "-C", d, "cat-file", "-e", f"{last_sha}^{{commit}}"], capture_output=True).returncode == 0
    base = last_sha if known else None
    if not known:
        since = (datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_DAYS)).isoformat()
        base = (git(d, "rev-list", "-1", f"--before={since}", "HEAD", check=False) or "").strip() or None
        print(f"ℹ {repo}: erster Lauf – berücksichtige die letzten {FIRST_RUN_DAYS} Tage")
    if base == head:
        print(f"✓ {repo}: keine neuen Commits")
        return [], head

    rng = f"{base}..{head}" if base else head
    files = (git(d, "diff", "--name-only", "--diff-filter=ACMR", base, head) if base
             else git(d, "ls-tree", "-r", "--name-only", head)).splitlines()

    groups = {}
    for f in files:
        parts = f.split("/")
        if len(parts) < 2 or parts[0] in SKIP_FOLDERS or parts[0].startswith("."):
            continue
        if not f.lower().endswith(CODE_EXT):
            continue
        groups.setdefault(parts[0], []).append(f)

    entries = []
    for folder, fs in groups.items():
        # letzter Commit, der diesen Ordner berührt hat
        info = git(d, "log", "-1", "--format=%cI%x1f%s", rng, "--", folder).strip()
        if not info:
            continue
        ciso, msg = info.split("\x1f", 1)
        date = datetime.fromisoformat(ciso).astimezone(TZ).strftime("%Y-%m-%d")
        msgs = git(d, "log", "--format=%s", rng, "--", folder).splitlines()
        custom = next((m for m in msgs if m and not GENERIC_MSG.match(m)), "")
        is_fix = bool(re.match(r"^fix\b", custom, re.I))

        key = f"{repo}/{folder}"
        name = names.get(key) or title_of(d, head, folder)
        is_new = not folder_exists(d, base, folder)
        version = find_version(d, head, fs)
        old_version = None if is_new else find_version(d, base, fs)
        bumped = bool(version) and version != old_version

        if custom:
            text = custom + (f" (V{version})" if bumped else "")
        elif is_new:
            text = f"Released V{version}" if version else "Added"
        elif bumped:
            text = f"Updated to V{version}"
        else:
            text = "Updated"

        entries.append({
            "date": date, "tool": html.escape(name), "text": html.escape(text),
            "type": "new" if is_new else "fix" if is_fix else "update",
            "category": category, "auto": True, "version": version, "key": key,
        })
        print(f"• {repo}: {entries[-1]['type']:6} {name} – {text}")
    return entries, head


def vtuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return ()


def merge(changelog, new):
    for e in new:
        same = next((x for x in changelog if x.get("auto") and x.get("key") == e["key"]
                     and x.get("date") == e["date"]), None)
        if same:
            if same.get("type") == "new":
                e["type"] = "new"
                if e["text"].startswith("Updated"):
                    e["text"] = html.escape(f"Released V{e['version']}") if e.get("version") else "Added"
            if same.get("version") and e.get("version") and vtuple(same["version"]) > vtuple(e["version"]):
                e["version"], e["text"] = same["version"], same["text"]
            changelog.remove(same)
        changelog.append(e)
    changelog.sort(key=lambda x: x.get("date", ""), reverse=True)
    return changelog[:MAX_ENTRIES]


def main():
    state = load_json(STATE, {})
    changelog = load_json(CHANGELOG, [])
    names = tool_names()
    all_new = []
    with tempfile.TemporaryDirectory() as wd:
        for repo, cat in REPOS.items():
            entries, head = process_repo(repo, cat, state.get(repo), names, wd)
            all_new += entries
            if head:
                state[repo] = head
    if all_new:
        changelog = merge(changelog, all_new)
        with open(CHANGELOG, "w", encoding="utf-8") as fh:
            json.dump(changelog, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    print(f"Fertig: {len(all_new)} Eintrag/Einträge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
