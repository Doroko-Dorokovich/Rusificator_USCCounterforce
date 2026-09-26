#!/usr/bin/env python3
"""
Replaces ONLY the term text in the Hungarian slot [2] with Russian,
using the cached translations. Leaves mLanguages[2] Name/Code as
"Hungarian"/"hu" UNCHANGED, so any UI button that targets the
language by name still finds a match.

Usage:
    python ru_replace2.py INPUT.txt CACHE.json OUTPUT.txt
"""
import json
import sys


def extract_quoted(line: str) -> str:
    first = line.index('"')
    last = line.rindex('"')
    return line[first + 1:last]


def make_quoted_line(template_line: str, new_value: str) -> str:
    first = template_line.index('"')
    prefix = template_line[:first + 1]
    return prefix + new_value + '"'


def transform(lines, cache):
    out = list(lines)
    term_blocks_done = 0
    missing = []

    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()

        if stripped == '0 string Languages':
            assert lines[i + 1].strip() == '1 Array Array (5 items)', (i, lines[i + 1])
            assert lines[i + 7].strip() == '[2]', (i, lines[i + 7])
            english = extract_quoted(lines[i + 4])
            russian = cache.get(english)
            if russian is None:
                missing.append(english)
                russian = english
            out[i + 8] = make_quoted_line(lines[i + 8], russian)
            term_blocks_done += 1
            i += 13
            continue

        # mLanguages block intentionally left untouched this time
        i += 1

    return out, {'term_blocks_done': term_blocks_done, 'missing': missing}


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    in_path, cache_path, out_path = sys.argv[1:4]

    with open(in_path, encoding='utf-8', newline='') as f:
        content = f.read()
    lines = content.split('\r\n')
    assert '\r\n'.join(lines) == content, 'unexpected line-ending format in this file'

    with open(cache_path, encoding='utf-8') as f:
        cache = json.load(f)

    new_lines, stats = transform(lines, cache)
    print('term blocks updated:', stats['term_blocks_done'])
    if stats['missing']:
        print(f"WARNING: {len(stats['missing'])} strings had no cached translation:")
        for m in stats['missing'][:10]:
            print('  -', m[:80])

    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        f.write('\r\n'.join(new_lines))
    print('wrote', out_path)


if __name__ == '__main__':
    main()
