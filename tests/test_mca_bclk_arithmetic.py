"""Width of the mca_fe_hw_params TDM bclk product.

By default the harness copies the retained audio/j713/kernel/mca.c into a
temporary tree, requires its snapshot SHA256, and applies
patches-kernel/j713-audio/mca-bclk-width.patch with
patch -p1 --fuzz=0 --forward from that tree's root. It then extracts the
else-branch assignment and compiles that RHS with the function's real
operand types. MCA_SOURCE bypasses the patch so a baseline or mutant file
can be measured directly. The retained file and its hash inventory stay
unchanged.

This checks evaluation width only. It does not validate hw_params, rewrite
the imported source, or qualify deployed binaries.

Shipped generic machine callers use 2*32, 8*32, and 16*16. The boundary
pairs below are synthetic and are not a claim about caller reachability
or about a J713 PCM overflow. J713 returns before this assignment.
The staged patch casts one operand to unsigned long before the multiply.
A cast applied to the product after the multiply still wraps.
"""

from hashlib import sha256
import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MCA = _ROOT / "audio" / "j713" / "kernel" / "mca.c"
_PATCH = _ROOT / "patches-kernel" / "j713-audio" / "mca-bclk-width.patch"
_MCA_SHA256 = "6ee2fade6fc237cedb211c4bd198891e10b4fabac947070dce9bc55307628846"

# (slot_width, slots, mathematical unsigned-long product)
_NORMAL = ((32, 2, 64), (32, 8, 256), (16, 16, 256))
# Synthetic. Not values supplied by the shipped machine callers.
_BOUNDARY = (
    (65536, 65536, 4294967296),
    (4294967295, 4294967295, 18446744065119617025),
)

_ASSIGN = re.compile(
    r"if\s*\(\s*cl->bclk_ratio\s*\)\s*"
    r"bclk_ratio\s*=\s*cl->bclk_ratio\s*;\s*"
    r"else\s*"
    r"bclk_ratio\s*=\s*(?P<rhs>[^;]+);",
    re.DOTALL,
)


def bclk_product_rhs(source_text):
    """Return the RHS of the mca_fe_hw_params else-branch product."""
    match = _ASSIGN.search(source_text)
    if match is None:
        raise AssertionError(
            "mca_fe_hw_params else-branch bclk_ratio assignment not found"
        )
    rhs = " ".join(match.group("rhs").split())
    if "tdm_slot_width" not in rhs or "tdm_slots" not in rhs:
        raise AssertionError("extracted RHS is not the slot product: %s" % rhs)
    return rhs


def mca_source_text(directory):
    """Return patched retained source, or MCA_SOURCE with no patch applied."""
    if "MCA_SOURCE" in os.environ:
        return Path(os.environ["MCA_SOURCE"]).read_text(encoding="utf-8")
    data = _DEFAULT_MCA.read_bytes()
    digest = sha256(data).hexdigest()
    if digest != _MCA_SHA256:
        raise AssertionError(
            "retained mca.c SHA256 %s != %s; refusing to patch" % (digest, _MCA_SHA256)
        )
    dest = directory / "audio" / "j713" / "kernel" / "mca.c"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(data)
    applied = subprocess.run(
        ["patch", "-p1", "--fuzz=0", "--forward", "-i", str(_PATCH)],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if applied.returncode != 0:
        raise AssertionError(applied.stderr or applied.stdout)
    if sha256(_DEFAULT_MCA.read_bytes()).hexdigest() != _MCA_SHA256:
        raise AssertionError("retained mca.c changed while applying the patch")
    return dest.read_text(encoding="utf-8")


def _rows(cases):
    return ",\n".join(
        "        { %uu, %uu, %uul }" % (width, slots, product)
        for width, slots, product in cases
    )


def _harness(rhs):
    return """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct sample {
    unsigned int tdm_slot_width;
    unsigned int tdm_slots;
    unsigned long expect;
};

static int run_group(const char *name, const struct sample *rows, size_t count)
{
    size_t i;

    for (i = 0; i < count; i++) {
        unsigned int tdm_slot_width = rows[i].tdm_slot_width;
        unsigned int tdm_slots = rows[i].tdm_slots;
        unsigned long bclk_ratio = %s;

        if (bclk_ratio != rows[i].expect) {
            fprintf(stderr,
                "%%s: %%u * %%u => %%lu, want %%lu\\n",
                name, tdm_slot_width, tdm_slots, bclk_ratio, rows[i].expect);
            return 1;
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    static const struct sample normal[] = {
%s
    };
    static const struct sample boundary[] = {
%s
    };
    const char *which;

    if (sizeof(unsigned int) != 4 || sizeof(unsigned long) != 8) {
        fprintf(stderr,
            "require sizeof(unsigned int)==4 and sizeof(unsigned long)==8, "
            "got %%zu and %%zu\\n",
            sizeof(unsigned int), sizeof(unsigned long));
        return 2;
    }
    if (argc != 2) {
        fprintf(stderr, "usage: %%s normal|boundary\\n", argv[0]);
        return 2;
    }
    which = argv[1];
    if (strcmp(which, "normal") == 0)
        return run_group("normal", normal, sizeof(normal) / sizeof(normal[0]));
    if (strcmp(which, "boundary") == 0)
        return run_group("boundary", boundary,
            sizeof(boundary) / sizeof(boundary[0]));
    fprintf(stderr, "unknown group %%s\\n", which);
    return 2;
}
""" % (rhs, _rows(_NORMAL), _rows(_BOUNDARY))


class RetainedMcaSnapshotTest(unittest.TestCase):
    def test_snapshot_hash_remains(self):
        data = _DEFAULT_MCA.read_bytes()
        self.assertEqual(sha256(data).hexdigest(), _MCA_SHA256)


class McaBclkArithmeticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory(prefix="mca-bclk-")
        source_text = mca_source_text(Path(cls._tmp.name))
        cls.rhs = bclk_product_rhs(source_text)
        src = Path(cls._tmp.name) / "bclk_ratio.c"
        cls.binary = Path(cls._tmp.name) / "bclk_ratio"
        src.write_text(_harness(cls.rhs), encoding="utf-8")
        compiler = os.environ.get("CC", "cc")
        compiled = subprocess.run(
            [compiler, "-std=c11", "-Wall", "-Werror", "-o", str(cls.binary), str(src)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if compiled.returncode != 0:
            raise AssertionError(compiled.stderr or compiled.stdout)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, group):
        result = subprocess.run(
            [str(self.binary), group],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(
            result.returncode,
            0,
            "RHS %r group %s failed:\\n%s%s" % (
                self.rhs, group, result.stdout, result.stderr),
        )

    def test_normal_slot_products(self):
        self._run("normal")

    def test_synthetic_32bit_overflow_boundaries(self):
        self._run("boundary")


if __name__ == "__main__":
    unittest.main()
