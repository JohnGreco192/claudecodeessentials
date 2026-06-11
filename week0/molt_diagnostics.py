"""
Moltbook diagnostics: fetch tracked posts and show verification challenges plus parsed candidate answers
This script does NOT submit answers; it helps inspect what the solver would try.
Usage: MOLTBOOK_API_KEY not required for read-only public post checks.
"""
import re
import json
import urllib.request

MEMORY_PATH = 'week0/MEMORY.md'


def words_to_num(text: str) -> int:
    ones = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9
    }
    teens = {
        'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19
    }
    tens = {
        'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90
    }
    scales = {'hundred': 100, 'thousand': 1000, 'million': 1000000}

    tokens = [t for t in re.split(r"[\s-]+", text.lower()) if t]
    total = 0
    current = 0
    for t in tokens:
        if t in ones:
            current += ones[t]
        elif t in teens:
            current += teens[t]
        elif t in tens:
            current += tens[t]
        elif t == 'hundred':
            if current == 0:
                current = 100
            else:
                current *= 100
        elif t in ('thousand', 'million'):
            mult = scales.get(t, 1000)
            if current == 0:
                total += mult
            else:
                total += current * mult
            current = 0
        else:
            continue
    return total + current


def extract_number_words_from_challenge(challenge: str):
    readable = re.sub(r"[^a-zA-Z0-9\s]", " ", challenge).lower()
    digits = [int(m) for m in re.findall(r"\d+", readable)]
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
                val = words_to_num(phrase)
                words.append(val)
            except Exception:
                pass
            i = j
        else:
            i += 1
    noisy = []
    for tok in re.split(r"\s+", challenge):
        cleaned = re.sub(r"[^a-z]", "", tok.lower())
        if cleaned in number_word_tokens:
            try:
                noisy.append(words_to_num(cleaned))
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
                        candidates = []
                        expr = re.sub(r"[^0-9+\-*/().\s]", "", challenge).strip()
                        if expr and re.search(r"[+\-*/]", expr):
                            try:
                                val = float(eval(expr))
                                candidates.append(f"{val:.2f}")
                            except Exception:
                                pass
                        nums = digs + words + noisy
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
