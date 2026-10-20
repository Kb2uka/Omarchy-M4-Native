"""Width of the mca_fe_hw_params TDM bclk product.

Extracts the else-branch assignment in mca_fe_hw_params and compiles that
RHS with the function's real operand types. This checks evaluation width
only. It does not validate hw_params or qualify hardware.

Shipped generic machine callers use 2*32, 8*32, and 16*16. The boundary
pairs below are synthetic and are not a claim about caller reachability
or about a J713 PCM overflow. J713 returns before this assignment.
A cast applied to the product after the multiply still wraps; the later
fix is to promote an operand before the multiply.
"""

import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MCA = _ROOT / "audio" / "j713" / "kernel" / "mca.c"
MCA_SOURCE = Path(os.environ.get("MCA_SOURCE", str(_DEFAULT_MCA)))

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


class McaBclkArithmeticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_text = MCA_SOURCE.read_text(encoding="utf-8")
        cls.rhs = bclk_product_rhs(source_text)
        cls._tmp = TemporaryDirectory(prefix="mca-bclk-")
        src = Path(cls._tmp.name) / "bclk_ratio.c"
        cls.binary = Path(cls._tmp.name) / "bclk_ratio"
        src.write_text(_harness(cls.rhs), encoding="utf-8")
        compiled = subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Werror", "-o", str(cls.binary), str(src)],
            capture_output=True,
            text=True,
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
