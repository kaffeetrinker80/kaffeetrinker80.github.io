#!/usr/bin/env python3
"""
Erzeugt/aktualisiert changelog.json automatisch aus einem Push.

Logik:
- Welche Tool-Ordner wurden geändert?  (z. B. theocratic/kh-winterservice/)
- Tool-Name aus der publicTools-Liste in index.html (Fallback: <title> oder Ordnername)
- Version aus APP_VERSION / "Version x.y.z" / Dateiname "-v.X.Y.Z"
- Typ: new (Ordner neu) / fix (Commit beginnt mit "fix") / update
- Pro Tool und Tag nur ein Eintrag (mehrere Uploads am selben Tag werden zusammengefasst)
"""
import html, json, os, re, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo

CHANGELOG = "changelog.json"
MAX_ENTRIES = 80
CATEGORIES = {"theocratic": "theocratic", "worktools": "work", "private": "projects", "nova": "nova"}
CODE_EXT = (".html", ".htm", ".js", ".css", ".json", ".md")
IGNORE_EXT = (".webmanifest",)
GENERIC_MSG = re.compile(r"^(add files via upload|update\b|create\b|delete\b|rename\b|merge\b|upload\b|initial commit)", re.I)
VERSION_PATTERNS = [
    re.compile(r"APP_VERSION\s*=\s*['\"]v?(\d+\.\d+(?:\.\d+)?)['\"]"),
    re.compile(r"\bversion\s*[:=]\s*['\"]v?(\d+\.\d+\.\d+)['\"]", re.I),
    re.compile(r"\bV(?:ersion)?\s?(\d+\.\d+\.\d+)\b"),
]
FILENAME_VERSION = re.compile(r"-v\.?(\d+\.\d+\.\d+)\.html?$", re.I)


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout if r.returncode == 0 else None


def exists_in(rev, path):
    return rev and subprocess.run(["git", "cat-file", "-e", f"{rev}:{path}"], capture_output=True).returncode == 0


def changed_files(before, after):
    if not before or set(before) == {"0"} or not exists_in(before, "."):
        before = (git("rev-parse", f"{after}~1", check=False) or "").strip() or None
    if before:
        out = git("diff", "--name-only", "--diff-filter=ACMR", before, after)
    else:
        out = git("ls-tree", "-r", "--name-only", after)
    return before, [p for p in out.splitlines() if p.strip()]


def tool_names():
    names = {}
    try:
        src = open("index.html", encoding="utf-8").read()
    except OSError:
        return names
    for m in re.finditer(r'name:\s*"([^"]+)"[^}]*?path:\s*"/([^"]+?)/?(?:index\.html)?"', src):
        names[m.group(2).strip("/")] = m.group(1)
    return names


def read_file(rev, path):
    if rev:
        return git("show", f"{rev}:{path}", check=False) or ""
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


def find_version(files, rev=None):
    for f in sorted(files, key=lambda p: (not p.endswith("index.html"), p)):
        m = FILENAME_VERSION.search(f)
        if m:
            return m.group(1)
        if f.lower().endswith((".html", ".htm", ".js")):
            text = read_file(rev, f)
            for pat in VERSION_PATTERNS:
                m = pat.search(text)
                if m:
                    return m.group(1)
    return None


def title_of(folder):
    text = read_file(None, f"{folder}/index.html")
    m = re.search(r"<title>([^<]+)</title>", text, re.I)
    if m:
        return m.group(1).strip()
    return folder.split("/")[-1].replace("-", " ").title()


def vtuple(v):
    return tuple(int(x) for x in v.split(".")) if v else ()


def main():
    before, after = os.environ.get("BEFORE", ""), os.environ.get("AFTER", "HEAD")
    message = (os.environ.get("COMMIT_MSG") or git("log", "-1", "--pretty=%B", after)).strip().splitlines()[0:1]
    message = message[0].strip() if message else ""
    before, files = changed_files(before, after)

    groups = {}
    for f in files:
        parts = f.split("/")
        if len(parts) < 3 or parts[0] not in CATEGORIES or f.endswith(IGNORE_EXT):
            continue
        if not f.lower().endswith(CODE_EXT):
            continue
        groups.setdefault("/".join(parts[:2]), []).append(f)

    if not groups:
        print("Keine Tool-Änderungen – nichts zu tun.")
        return 0

    try:
        entries = json.load(open(CHANGELOG, encoding="utf-8"))
    except (OSError, ValueError):
        entries = []

    today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
    names = tool_names()
    custom = message and not GENERIC_MSG.match(message)
    is_fix = bool(re.match(r"^fix\b", message, re.I))

    for folder, fs in groups.items():
        cat = CATEGORIES[folder.split("/")[0]]
        name = names.get(folder) or title_of(folder)
        is_new = not exists_in(before, folder)
        version = find_version(fs)
        old_version = None if is_new else find_version(fs, before)
        bumped = version and version != old_version

        if custom:
            text = message + (f" (V{version})" if bumped else "")
        elif is_new:
            text = f"Released V{version}" if version else "Added"
        elif bumped:
            text = f"Updated to V{version}"
        else:
            text = "Updated"

        etype = "new" if is_new else "fix" if is_fix else "update"
        entry = {"date": today, "tool": html.escape(name), "text": html.escape(text),
                 "type": etype, "category": cat, "auto": True, "version": version}

        # Same tool, same day, auto-generated -> merge instead of adding a new line
        same = next((e for e in entries if e.get("auto") and e.get("date") == today and e.get("tool") == entry["tool"]), None)
        if same:
            if same.get("type") == "new":
                entry["type"] = "new"
                if not custom and version:
                    entry["text"] = html.escape(f"Released V{version}")
            if not custom and same.get("version") and version and vtuple(same["version"]) > vtuple(version):
                entry["version"], entry["text"] = same["version"], same["text"]
            entries.remove(same)
        entries.append(entry)
        print(f"{entry['type']:7} {name}: {text}")

    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    with open(CHANGELOG, "w", encoding="utf-8") as fh:
        json.dump(entries[:MAX_ENTRIES], fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
