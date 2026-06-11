"""
Moltbook diagnostics: fetch tracked posts and show verification challenges plus parsed candidate answers
This script does NOT submit answers; it helps inspect what the solver would try.
Usage: MOLTBOOK_API_KEY not required for read-only public post checks.
"""
import re
import json
import urllib.request
from week0.post_daily_close import words_to_num if 'words_to_num' else None

from week0 import post_daily_close as p

MEMORY_PATH = p.MEMORY_PATH


def extract_number_words_from_challenge(challenge: str):
    # reuse post_daily_close parsing heuristics via importing functions where possible
    readable = re.sub(r"[^a-zA-Z0-9\s]", " ", challenge).lower()
    # digits
    digits = [int(m) for m in re.findall(r"\d+", readable)]
    # word-seqs
    number_word_tokens = set([
        'zero','one','two','three','four','five','six','seven','eight','nine',
        'ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen',
        'twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety',
        'hundred','thousand','million'
    ])
    tokens = readable.split()
    i = 0
    words = []
    while i < len(tokens):
        if tokens[i] in number_word_tokens:
            j = i
            while j < len(tokens) and tokens[j] in number_word_tokens:
                j += 1
            phrase = " ".join(tokens[i:j])
            try:
                val = p.words_to_num(phrase)
                words.append(val)
            except Exception:
                pass
            i = j
        else:
            i += 1
    # noisy token scan
    noisy = []
    for tok in re.split(r"\s+", challenge):
        cleaned = re.sub(r"[^a-z]", "", tok.lower())
        if cleaned in number_word_tokens:
            try:
                noisy.append(p.words_to_num(cleaned))
            except Exception:
                pass
    return digits, words, noisy


def diagnostics():
    with open(MEMORY_PATH) as f:
        content = f.read()
    m = re.search(r"## Own Posts\n((?:- .+\n?)*)", content)
    if not m:
        print('No own posts found in MEMORY.md')
        return
    for line in m.group(1).strip().splitlines():
        parts = [p.strip() for p in line.lstrip('- ').split('|')]
        if len(parts) >= 2:
            pid = parts[1]
            url = f"https://www.moltbook.com/api/v1/posts/{pid}"
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    data = json.load(r)
                    post = data.get('post', data)
                    ver = post.get('verification') or {}
                    challenge = ver.get('challenge_text') or ver.get('challenge') or ''
                    print('\nPOST', pid, 'title=', post.get('title'))
                    print('  verification_status=', post.get('verification_status'))
                    print('  is_deleted=', post.get('is_deleted'))
                    if challenge:
                        print('  challenge:', challenge)
                        digs, words, noisy = extract_number_words_from_challenge(challenge)
                        print('  parsed digits:', digs)
                        print('  parsed word-numbers:', words)
                        print('  parsed noisy word-numbers:', noisy)
                        # generate candidate list (non-submitting) using internal helper heuristics
                        candidates = []
                        # try evaluating clean expr
                        expr = re.sub(r"[^0-9+\-*/().\s]", "", challenge).strip()
                        if expr and re.search(r"[+\-*/]", expr):
                            try:
                                val = float(eval(expr))
                                candidates.append(f"{val:.2f}")
                            except Exception:
                                pass
                        nums = digits + words + noisy
                        if nums:
                            a = nums[0]
                            if len(nums) > 1:
                                b = nums[1]
                                candidates += [f"{(a+b):.2f}", f"{(a-b):.2f}", f"{(b-a):.2f}", f"{(a*b):.2f}"]
                            candidates.append(f"{a:.2f}")
                        print('  candidate answers (sample):', candidates[:10])
                    else:
                        print('  no challenge present')
            except Exception as e:
                print('  failed to fetch', pid, e)

if __name__ == '__main__':
    diagnostics()
