import random
r = random.Random(42)
words = ["alpha","beta","gamma","delta","epsilon","zeta","omega","lorem","ipsum","dolor","sit","amet"]
print("---")
print("version: 1")
print("meta:")
print("  author: oqed-bench")
print("  count: 20000")
print("items:")
for i in range(20000):
    w1 = words[r.randrange(len(words))]
    w2 = words[r.randrange(len(words))]
    style = r.randrange(4)
    if style == 0:
        s = f'"{w1} {w2} {i}"'
    elif style == 1:
        s = f"'{w1}-{w2}-{i}'"
    else:
        s = f"{w1}_{w2}_{i}"
    print(f"  - id: {i}")
    print(f"    name: {s}")
    print(f"    value: {r.randrange(-1000000,1000000)}")
    print(f"    ratio: {(r.random()*1000):.6f}")
    print(f"    flag: {'true' if (i & 1) else 'false'}")
    if i % 5 == 0:
        print(f"    tags: [{w1}, {w2}, tag{i % 97}]")
        print(f"    nested: {{a: {i}, b: {w1}, c: [{i % 13}, {(i*7) % 101}]}}")
    else:
        print(f"    desc: plain scalar number {i} with {w1}")
print("footer:")
print("  done: true")
