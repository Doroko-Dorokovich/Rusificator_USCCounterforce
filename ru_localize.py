#!/usr/bin/env python3
"""
Adds Russian as a 6th language to a Unity I2.Loc UABEA text dump
(LanguageSourceData -> mTerms / mLanguages).

Usage:
    pip install deep-translator
    python ru_localize.py INPUT.txt OUTPUT.txt

Resumable: translations are cached in <INPUT>.ru_cache.json, so if the
run is interrupted (network hiccup, rate limit, Ctrl+C) just re-run the
same command and it will pick up where it left off.

Test the mechanics without calling any translation service:
    python ru_localize.py INPUT.txt OUTPUT.txt --dry-run

Translate only the first N unique strings (useful for a quick sanity
check before committing to the full 3000+ call run):
    python ru_localize.py INPUT.txt OUTPUT.txt --limit 50
"""
import argparse
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# 1. Tag / placeholder protection
# ---------------------------------------------------------------------------
# Anything matching these patterns must survive translation completely
# unchanged: <color=...>, <i>, <b>, </i>, </b>, </color>, {0}..{9},
# the literal two-character escape \n, and bracketed key-bind /
# UI tokens like [LMB], [RMB], [SHIFT], [Escape], [0]-[9].
PROTECT_RE = re.compile(
    r'<[^<>]+>'        # <color=#F00>, </color>, <i>, </i>, <b>, </b> ...
    r'|\{[0-9]+\}'     # {0} {1} ...
    r'|\\n'            # literal backslash-n (two chars, NOT a real newline)
    r'|\[[^\[\]\s]{1,20}\]'  # [LMB] [RMB] [SHIFT] [Escape] [0] [9] ...
)

# Private-Use-Area characters as placeholders: translators generally leave
# these completely alone (they don't look like any real-language content).
_PUA_BASE = 0xE000


def protect(text: str):
    """Replace protected substrings with PUA placeholder chars.
    Returns (protected_text, tokens) where tokens[i] is the original text
    that replaced PUA char chr(_PUA_BASE + i)."""
    tokens = []

    def _sub(m):
        tokens.append(m.group(0))
        return chr(_PUA_BASE + len(tokens) - 1)

    protected = PROTECT_RE.sub(_sub, text)
    return protected, tokens


def restore(translated: str, tokens):
    """Put the protected substrings back. Handles the (rare) case where
    the translator dropped or duplicated a placeholder character."""
    out = []
    for ch in translated:
        code = ord(ch)
        idx = code - _PUA_BASE
        if 0 <= idx < len(tokens):
            out.append(tokens[idx])
        else:
            out.append(ch)
    result = ''.join(out)
    # Safety net: if the translator ate a placeholder char entirely,
    # append whatever tokens are missing at the end rather than lose them.
    for i, tok in enumerate(tokens):
        if chr(_PUA_BASE + i) not in translated:
            result += tok
    return result


# ---------------------------------------------------------------------------
# 2. Structural parsing / rebuilding of the UABEA dump
#    (validated against the real file structure - see chat)
# ---------------------------------------------------------------------------

def extract_quoted(line: str) -> str:
    first = line.index('"')
    last = line.rindex('"')
    return line[first + 1:last]


def make_quoted_line(template_line: str, new_value: str) -> str:
    first = template_line.index('"')
    prefix = template_line[:first + 1]
    return prefix + new_value + '"'


def collect_english_terms(lines):
    """Return list of English strings, one per term block, in file order."""
    out = []
    for i, line in enumerate(lines):
        if line.strip() == '0 string Languages':
            out.append(extract_quoted(lines[i + 4]))
    return out


def transform(lines, translate_fn, new_lang_name="Russian", new_lang_code="ru"):
    out = []
    i = 0
    n = len(lines)
    term_blocks_done = 0
    mlang_done = False

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == '0 string Languages':
            assert lines[i + 1].strip() == '1 Array Array (5 items)', (i, lines[i + 1])
            assert lines[i + 2].strip() == '0 int size = 5', (i, lines[i + 2])
            assert lines[i + 11].strip() == '[4]', (i, lines[i + 11])

            english = extract_quoted(lines[i + 4])
            russian = translate_fn(english)

            out.append(lines[i])
            out.append(lines[i + 1].replace('(5 items)', '(6 items)'))
            out.append(lines[i + 2].replace('size = 5', 'size = 6'))
            out.extend(lines[i + 3:i + 13])
            out.append(lines[i + 11].replace('[4]', '[5]'))
            out.append(make_quoted_line(lines[i + 4], russian))

            i += 13
            continue

        if stripped == '0 vector Flags':
            assert lines[i + 1].strip() == '1 Array Array (5 items)', (i, lines[i + 1])
            assert lines[i + 2].strip() == '0 int size = 5', (i, lines[i + 2])
            assert lines[i + 11].strip() == '[4]', (i, lines[i + 11])

            out.append(lines[i])
            out.append(lines[i + 1].replace('(5 items)', '(6 items)'))
            out.append(lines[i + 2].replace('size = 5', 'size = 6'))
            out.extend(lines[i + 3:i + 13])
            out.append(lines[i + 11].replace('[4]', '[5]'))
            out.append(lines[i + 12])  # duplicate default flag value

            i += 13
            term_blocks_done += 1
            continue

        if stripped == '0 LanguageData mLanguages':
            assert lines[i + 1].strip() == '1 Array Array (5 items)', (i, lines[i + 1])
            assert lines[i + 2].strip() == '0 int size = 5', (i, lines[i + 2])
            assert lines[i + 23].strip() == '[4]', (i, lines[i + 23])

            out.append(lines[i])
            out.append(lines[i + 1].replace('(5 items)', '(6 items)'))
            out.append(lines[i + 2].replace('size = 5', 'size = 6'))
            out.extend(lines[i + 3:i + 28])

            out.append(lines[i + 23].replace('[4]', '[5]'))
            out.append(lines[i + 24])
            out.append(make_quoted_line(lines[i + 25], new_lang_name))
            out.append(make_quoted_line(lines[i + 26], new_lang_code))
            out.append(lines[i + 27])

            i += 28
            mlang_done = True
            continue

        out.append(line)
        i += 1

    return out, {'term_blocks_done': term_blocks_done, 'mlang_done': mlang_done}


# ---------------------------------------------------------------------------
# 3. Translation with caching + retries
# ---------------------------------------------------------------------------

def load_cache(path):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(path, cache):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def translate_text_real(text, translator):
    if text.strip() == '':
        return text
    protected, tokens = protect(text)
    result = translator.translate(protected)
    if result is None:
        raise RuntimeError('translator returned None')
    return restore(result, tokens)


def build_cache(unique_terms, cache_path, dry_run, limit, delay, max_retries):
    cache = load_cache(cache_path)

    if dry_run:
        translator = None
    else:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='en', target='ru')

    todo = [t for t in unique_terms if t not in cache]
    if limit is not None:
        todo = todo[:limit]

    print(f'{len(unique_terms)} unique English strings, '
          f'{len(unique_terms) - len(todo)} already cached, '
          f'{len(todo)} to translate now.')

    for n, text in enumerate(todo, 1):
        for attempt in range(1, max_retries + 1):
            try:
                if dry_run:
                    cache[text] = '[RU] ' + text
                else:
                    cache[text] = translate_text_real(text, translator)
                break
            except Exception as e:
                wait = delay * (2 ** (attempt - 1))
                print(f'  [{n}/{len(todo)}] error ({e!r}), '
                      f'retry {attempt}/{max_retries} in {wait:.1f}s', file=sys.stderr)
                time.sleep(wait)
        else:
            print(f'  [{n}/{len(todo)}] FAILED after {max_retries} retries, '
                  f'leaving untranslated for now: {text[:60]!r}', file=sys.stderr)
            continue

        if n % 20 == 0 or n == len(todo):
            save_cache(cache_path, cache)
            print(f'  [{n}/{len(todo)}] cached, saved progress.')

        if not dry_run:
            time.sleep(delay)

    save_cache(cache_path, cache)
    return cache


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--cache', default=None,
                     help='cache JSON path (default: <input>.ru_cache.json)')
    ap.add_argument('--dry-run', action='store_true',
                     help="don't call any translation service, just prefix "
                          "with '[RU] ' - use this to sanity check the file "
                          "structure and script mechanics first")
    ap.add_argument('--limit', type=int, default=None,
                     help='only translate the first N new unique strings this run')
    ap.add_argument('--delay', type=float, default=0.4,
                     help='seconds to sleep between translation calls (default 0.4)')
    ap.add_argument('--max-retries', type=int, default=5)
    args = ap.parse_args()

    cache_path = args.cache or (args.input + '.ru_cache.json')

    with open(args.input, encoding='utf-8', newline='') as f:
        content = f.read()
    lines = content.split('\r\n')
    assert '\r\n'.join(lines) == content, 'unexpected line-ending format in this file'

    unique_terms = sorted(set(collect_english_terms(lines)))
    cache = build_cache(unique_terms, cache_path, args.dry_run, args.limit,
                         args.delay, args.max_retries)

    missing = [t for t in unique_terms if t not in cache]
    if missing:
        print(f'\n{len(missing)} strings are still untranslated '
              f'(left as English for now). Re-run the same command to retry them.')
        for t in missing:
            cache.setdefault(t, t)  # fall back to English rather than crash

    def translate_fn(english):
        return cache.get(english, english)

    new_lines, stats = transform(lines, translate_fn)
    print('term blocks updated:', stats['term_blocks_done'])
    print('mLanguages updated:', stats['mlang_done'])

    with open(args.output, 'w', encoding='utf-8', newline='') as f:
        f.write('\r\n'.join(new_lines))
    print('wrote', args.output)


if __name__ == '__main__':
    main()
