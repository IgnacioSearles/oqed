import asyncio

from oqed import init, project
from oqed.providers import Completion

LIB = "unsigned mix(unsigned x) { for (int i = 0; i < 64; i++) x = x * 2654435761u ^ (x >> 13); return x; }\n"
MAIN = """#include <stdio.h>
unsigned mix(unsigned x);
int main(void) {
    unsigned h = 0;
    for (unsigned i = 0; i < 30000000; i++) h += mix(i);
    printf("%u\\n", h);
    return 0;
}
"""


class Scripted:
    """Replies in order, recording what it was sent."""

    model = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    async def complete(self, messages, effort=None, max_tokens=0):
        self.seen.append(list(messages))
        return Completion(text=self.replies.pop(0), model=self.model, model_ms=0, cost_usd=0.001)


def test_init_feeds_failures_back_until_the_config_works(tmp_path):
    (tmp_path / "lib.c").write_text(LIB)
    (tmp_path / "main.c").write_text(MAIN)
    (tmp_path / "README.md").write_text("Build with `cc -O2 -o app main.c lib.c`.\n")
    model = Scripted(
        [
            # Doesn't compile through $CC: oqed-cc sees nothing.
            '<toml>\nbuild = "cc -O2 -o app main.c lib.c"\nrun = "./app"\n</toml>',
            # Writes to /tmp: copies of the project run in parallel.
            '<toml>\nbuild = "$CC -O2 -o app main.c lib.c"\nrun = "./app > /tmp/out"\n</toml>',
            '<toml>\nbuild = "$CC -O2 -o app main.c lib.c"\nrun = "./app"\n</toml>',
        ]
    )
    spent = asyncio.run(init.init(tmp_path, model, log=lambda *_: None))
    assert spent == 0.003
    assert "README.md" in model.seen[0][1]["content"] and "Build with" in model.seen[0][1]["content"]
    feedback = [m[-1]["content"] for m in model.seen[1:]]
    assert "compiled nothing through $CC" in feedback[0]
    assert "/tmp" in feedback[1]
    p = project.load(tmp_path)
    assert (p.build, p.run) == ("$CC -O2 -o app main.c lib.c", "./app")


def test_init_gives_up_and_leaves_no_config(tmp_path):
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")
    model = Scripted(['<toml>\nbuild = "true"\nrun = "true"\n</toml>'] * 2)
    try:
        asyncio.run(init.init(tmp_path, model, attempts=2, log=lambda *_: None))
    except RuntimeError as e:
        assert "no working oqed.toml after 2 attempts" in str(e)
    else:
        raise AssertionError("expected init to give up")
    assert not (tmp_path / project.CONFIG).exists()
