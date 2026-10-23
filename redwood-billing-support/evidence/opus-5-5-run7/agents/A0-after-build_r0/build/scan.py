import re,sys,collections
from flatten import *
terms = sys.argv[1:]
pat = re.compile("|".join(terms), re.I)
rows=[]
for p in all_records():
    t = text_of(p)
    n = len(pat.findall(t))
    if n: rows.append((n, str(p.relative_to(R)), len(t)))
rows.sort(reverse=True)
for r in rows[:int(__import__('os').environ.get('TOP','60'))]: print(*r)
print(len(rows), 'records')
