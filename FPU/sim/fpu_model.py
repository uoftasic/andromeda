"""Bit-exact RV32F reference model for the fpu_top testbench.

Every arithmetic result is computed exactly with fractions.Fraction and then
rounded once, so the model does not depend on the host FPU or on numpy. It
follows the RISC-V F extension (unprivileged spec, chapter "F"):

- NaN results are the canonical quiet NaN 0x7FC00000.
- Tininess is detected after rounding, so UF = tiny-after-rounding AND inexact.
- (inf * 0) + c raises NV even when c is a quiet NaN.
- FMIN/FMAX return the non-NaN operand when exactly one input is NaN, and treat
  -0 as smaller than +0.
- FCVT to integer saturates and raises NV (and not NX) on NaN or out of range.

This file has no cocotb dependency. Run it directly to self-check the model:

    python3 fpu_model.py
"""

from fractions import Fraction
from math import isqrt

# fpu_op_e from andromeda_pkg.sv, in encoding order.
OP_NAMES = [
    "ADD", "SUB", "MUL", "DIV", "SQRT",
    "MADD", "MSUB", "NMSUB", "NMADD",
    "SGNJ", "SGNJN", "SGNJX", "MIN", "MAX",
    "EQ", "LT", "LE", "CLASS",
    "CVT_W_S", "CVT_WU_S", "CVT_S_W", "CVT_S_WU",
    "MV_X_W", "MV_W_X",
]  # fmt: skip
(
    ADD, SUB, MUL, DIV, SQRT,
    MADD, MSUB, NMSUB, NMADD,
    SGNJ, SGNJN, SGNJX, MIN, MAX,
    EQ, LT, LE, CLASS,
    CVT_W_S, CVT_WU_S, CVT_S_W, CVT_S_WU,
    MV_X_W, MV_W_X,
) = range(len(OP_NAMES))  # fmt: skip
NUM_OPS = len(OP_NAMES)

# Ops that round, so they read rm and trap on a reserved rounding mode.
ROUNDING_OPS = (ADD, SUB, MUL, DIV, SQRT, MADD, MSUB, NMSUB, NMADD,
                CVT_W_S, CVT_WU_S, CVT_S_W, CVT_S_WU)  # fmt: skip
FMA_OPS = (MADD, MSUB, NMSUB, NMADD)
# Number of FP operands each op reads (rs1, rs2, rs3).
NUM_SRCS = {op: 2 for op in range(NUM_OPS)}
NUM_SRCS.update({SQRT: 1, CLASS: 1, CVT_W_S: 1, CVT_WU_S: 1, CVT_S_W: 1, CVT_S_WU: 1,
                 MV_X_W: 1, MV_W_X: 1})  # fmt: skip
NUM_SRCS.update({op: 3 for op in FMA_OPS})

# Rounding modes (rm_e)
RNE, RTZ, RDN, RUP, RMM, DYN = 0, 1, 2, 3, 4, 7
RM_NAMES = {0: "RNE", 1: "RTZ", 2: "RDN", 3: "RUP", 4: "RMM", 5: "rsv5", 6: "rsv6", 7: "DYN"}

# fflags bits {NV, DZ, OF, UF, NX}
NV, DZ, OF, UF, NX = 16, 8, 4, 2, 1

MASK32 = 0xFFFFFFFF
SIGN = 0x80000000
QNAN = 0x7FC00000
PINF = 0x7F800000
MAX_FINITE = 0x7F7FFFFF

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
UINT32_MAX = 2**32 - 1


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def sign_of(x):
    return (x >> 31) & 1


def exp_of(x):
    return (x >> 23) & 0xFF


def man_of(x):
    return x & 0x7FFFFF


def is_nan(x):
    return exp_of(x) == 0xFF and man_of(x) != 0


def is_snan(x):
    return is_nan(x) and not (man_of(x) >> 22) & 1


def is_inf(x):
    return exp_of(x) == 0xFF and man_of(x) == 0


def is_zero(x):
    return (x & 0x7FFFFFFF) == 0


def is_subnormal(x):
    return exp_of(x) == 0 and man_of(x) != 0


def pow2(n):
    return Fraction(1 << n) if n >= 0 else Fraction(1, 1 << -n)


def value(x):
    """Exact value of a finite float32 bit pattern."""
    e, m = exp_of(x), man_of(x)
    mag = m * pow2(-149) if e == 0 else (m | (1 << 23)) * pow2(e - 150)
    return -mag if sign_of(x) else mag


def to_signed(x):
    return x - (1 << 32) if x & SIGN else x


def describe(x):
    """Human-readable float32: hex plus a short decimal or class name."""
    if is_nan(x):
        kind = "sNaN" if is_snan(x) else "qNaN"
        return f"{x:#010x} ({'-' if sign_of(x) else ''}{kind})"
    if is_inf(x):
        return f"{x:#010x} ({'-' if sign_of(x) else '+'}inf)"
    return f"{x:#010x} ({float(value(x)):.9g}{', -0' if x == SIGN else ''})"


def describe_flags(f):
    names = [n for n, bit in (("NV", NV), ("DZ", DZ), ("OF", OF), ("UF", UF), ("NX", NX)) if f & bit]
    return "{" + ",".join(names) + "}" if names else "{}"


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------
def round_int(q, neg, rm):
    """Round the non-negative Fraction q to an integer. Returns (n, inexact).

    neg is the sign of the full value, which RDN and RUP need.
    """
    n = q.numerator // q.denominator
    rem = q - n
    if rem == 0:
        return n, False
    half = Fraction(1, 2)
    if rm == RNE:
        up = rem > half or (rem == half and n & 1)
    elif rm == RTZ:
        up = False
    elif rm == RDN:
        up = neg
    elif rm == RUP:
        up = not neg
    elif rm == RMM:
        up = rem >= half
    else:
        raise ValueError(f"reserved rounding mode {rm}")
    return n + int(up), True


def floor_log2(mag):
    e = mag.numerator.bit_length() - mag.denominator.bit_length()
    if pow2(e) > mag:
        e -= 1
    return e


def round_f32(neg, mag, rm):
    """Round a positive exact magnitude to float32. Returns (bits, flags)."""
    sign = SIGN if neg else 0
    e = floor_log2(mag)

    # Tininess after rounding: round to 24 bits with an unbounded exponent.
    n_u, _ = round_int(mag / pow2(e - 23), neg, rm)
    tiny = n_u * pow2(e - 23) < pow2(-126)

    eq = max(e, -126)  # subnormals share the quantum of the smallest normal
    n, inexact = round_int(mag / pow2(eq - 23), neg, rm)
    if n == 1 << 24:  # mantissa carried out
        n >>= 1
        eq += 1

    if eq > 127:
        to_inf = rm in (RNE, RMM) or (rm == RUP and not neg) or (rm == RDN and neg)
        return sign | (PINF if to_inf else MAX_FINITE), OF | NX

    flags = 0
    if inexact:
        flags |= NX
        if tiny:
            flags |= UF
    if n >= 1 << 23:
        bits = ((eq + 127) << 23) | (n - (1 << 23))
    else:
        bits = n  # subnormal or zero, eq == -126
    return sign | bits, flags


def round_value(v, rm):
    """Round a non-zero signed Fraction to float32."""
    return round_f32(v < 0, abs(v), rm)


def zero_sum(rm):
    """Sign of an exact zero sum of operands that are not both the same zero."""
    return (SIGN if rm == RDN else 0), 0


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------
def nan_result(*xs):
    return QNAN, NV if any(is_snan(x) for x in xs) else 0


def f_add(a, b, rm):
    if is_nan(a) or is_nan(b):
        return nan_result(a, b)
    if is_inf(a) and is_inf(b):
        return (QNAN, NV) if sign_of(a) != sign_of(b) else (a, 0)
    if is_inf(a):
        return a, 0
    if is_inf(b):
        return b, 0
    s = value(a) + value(b)
    if s == 0:
        if is_zero(a) and is_zero(b) and sign_of(a) == sign_of(b):
            return a & SIGN, 0
        return zero_sum(rm)
    return round_value(s, rm)


def f_mul(a, b, rm):
    if is_nan(a) or is_nan(b):
        return nan_result(a, b)
    if (is_inf(a) and is_zero(b)) or (is_zero(a) and is_inf(b)):
        return QNAN, NV
    sign = (sign_of(a) ^ sign_of(b)) << 31
    if is_inf(a) or is_inf(b):
        return sign | PINF, 0
    if is_zero(a) or is_zero(b):
        return sign, 0
    return round_value(value(a) * value(b), rm)


def f_div(a, b, rm):
    if is_nan(a) or is_nan(b):
        return nan_result(a, b)
    if (is_inf(a) and is_inf(b)) or (is_zero(a) and is_zero(b)):
        return QNAN, NV
    sign = (sign_of(a) ^ sign_of(b)) << 31
    if is_inf(a):
        return sign | PINF, 0
    if is_inf(b):
        return sign, 0
    if is_zero(b):
        return sign | PINF, DZ
    if is_zero(a):
        return sign, 0
    return round_value(value(a) / value(b), rm)


def f_sqrt(a, rm):
    if is_nan(a):
        return nan_result(a)
    if is_zero(a):
        return a, 0  # sqrt(-0) = -0
    if sign_of(a):
        return QNAN, NV
    if is_inf(a):
        return a, 0
    v = value(a)
    k = 200  # far more fraction bits than any float32 rounding boundary needs
    n = v.numerator * 4**k // v.denominator  # exact: the denominator is a power of 2
    r = isqrt(n)
    if r * r == n:
        mag = Fraction(r, 2**k)
    else:
        # The true root lies strictly inside (r, r+1) / 2^k and no rounding
        # boundary does, so any point inside gives the same rounding.
        mag = Fraction(2 * r + 1, 2 ** (k + 1))
    return round_f32(False, mag, rm)


def f_fma(op, a, b, c, rm):
    neg_prod = op in (NMSUB, NMADD)
    neg_c = op in (MSUB, NMADD)
    inv_prod = (is_inf(a) and is_zero(b)) or (is_zero(a) and is_inf(b))
    if inv_prod:
        return QNAN, NV  # even when c is a quiet NaN
    if is_nan(a) or is_nan(b) or is_nan(c):
        return nan_result(a, b, c)
    ps = sign_of(a) ^ sign_of(b) ^ int(neg_prod)
    cs = sign_of(c) ^ int(neg_c)
    if is_inf(a) or is_inf(b):
        if is_inf(c) and cs != ps:
            return QNAN, NV
        return (ps << 31) | PINF, 0
    if is_inf(c):
        return (cs << 31) | PINF, 0
    vp = abs(value(a) * value(b)) * (-1 if ps else 1)
    vc = abs(value(c)) * (-1 if cs else 1)
    s = vp + vc
    if s == 0:
        if (is_zero(a) or is_zero(b)) and is_zero(c) and ps == cs:
            return ps << 31, 0
        return zero_sum(rm)
    return round_value(s, rm)


def f_minmax(op, a, b):
    flags = NV if is_snan(a) or is_snan(b) else 0
    if is_nan(a) and is_nan(b):
        return QNAN, flags
    if is_nan(a):
        return b, flags
    if is_nan(b):
        return a, flags
    # Order by value, with -0 below +0
    key_a = (value(a), -sign_of(a))
    key_b = (value(b), -sign_of(b))
    if op == MIN:
        return (a if key_a <= key_b else b), flags
    return (a if key_a >= key_b else b), flags


def f_cmp(op, a, b):
    if is_nan(a) or is_nan(b):
        if op == EQ:
            return 0, NV if is_snan(a) or is_snan(b) else 0
        return 0, NV  # FLT/FLE signal on any NaN
    va, vb = value(a), value(b)
    if op == EQ:
        return int(va == vb), 0
    if op == LT:
        return int(va < vb), 0
    return int(va <= vb), 0


def f_class(a):
    neg = sign_of(a)
    if is_inf(a):
        bit = 0 if neg else 7
    elif is_nan(a):
        bit = 8 if is_snan(a) else 9
    elif is_zero(a):
        bit = 3 if neg else 4
    elif is_subnormal(a):
        bit = 2 if neg else 5
    else:
        bit = 1 if neg else 6
    return 1 << bit, 0


def f_to_int(op, a, rm):
    unsigned = op == CVT_WU_S
    lo, hi = (0, UINT32_MAX) if unsigned else (INT32_MIN, INT32_MAX)
    if is_nan(a):
        return hi & MASK32, NV
    if is_inf(a):
        return (lo if sign_of(a) else hi) & MASK32, NV
    v = value(a)
    n, inexact = round_int(abs(v), v < 0, rm)
    r = -n if v < 0 else n
    if r < lo:
        return lo & MASK32, NV
    if r > hi:
        return hi & MASK32, NV
    return r & MASK32, NX if inexact else 0


def f_from_int(op, x, rm):
    v = x if op == CVT_S_WU else to_signed(x)
    if v == 0:
        return 0, 0
    return round_value(Fraction(v), rm)


def sgnj(op, a, b):
    if op == SGNJ:
        s = b & SIGN
    elif op == SGNJN:
        s = ~b & SIGN
    else:
        s = (a ^ b) & SIGN
    return (a & 0x7FFFFFFF) | s, 0


def fpu_model(op, a, b, c, rm):
    """Result and fflags for one op. rm must already be resolved (not DYN)."""
    if op == ADD:
        return f_add(a, b, rm)
    if op == SUB:
        return f_add(a, b ^ SIGN, rm)
    if op == MUL:
        return f_mul(a, b, rm)
    if op == DIV:
        return f_div(a, b, rm)
    if op == SQRT:
        return f_sqrt(a, rm)
    if op in FMA_OPS:
        return f_fma(op, a, b, c, rm)
    if op in (SGNJ, SGNJN, SGNJX):
        return sgnj(op, a, b)
    if op in (MIN, MAX):
        return f_minmax(op, a, b)
    if op in (EQ, LT, LE):
        return f_cmp(op, a, b)
    if op == CLASS:
        return f_class(a)
    if op in (CVT_W_S, CVT_WU_S):
        return f_to_int(op, a, rm)
    if op in (CVT_S_W, CVT_S_WU):
        return f_from_int(op, a, rm)
    if op in (MV_X_W, MV_W_X):
        return a, 0
    raise ValueError(f"op {op} is not an fpu_op_e")


def resolve_rm(op, rm_field, frm):
    """Effective rounding mode, or None when the op must trap as illegal."""
    rm = frm if rm_field == DYN else rm_field
    if op not in ROUNDING_OPS:
        return RNE  # unused
    if rm in (5, 6, 7):
        return None
    return rm


# ---------------------------------------------------------------------------
# Hand-worked answers. They pin down the model; test_fpu_top.py also issues
# them to the DUT.
#   (op, rs1, rs2, rs3, rm, expected data, expected fflags)
# ---------------------------------------------------------------------------
ONE, TWO, THREE, HALF = 0x3F800000, 0x40000000, 0x40400000, 0x3F000000
MIN_NORM, MIN_SUB = 0x00800000, 0x00000001
SNAN = 0x7FA00000

KNOWN_ANSWERS = [
    (ADD, ONE, TWO, 0, RNE, THREE, 0),
    (ADD, ONE, 0x33800000, 0, RNE, ONE, NX),             # 1 + 2^-24: tie, stays even
    (ADD, ONE, 0x33800000, 0, RUP, 0x3F800001, NX),
    (ADD, ONE, 0x33800000, 0, RMM, 0x3F800001, NX),      # tie, away from zero
    (SUB, ONE, ONE, 0, RNE, 0x00000000, 0),              # x - x = +0
    (SUB, ONE, ONE, 0, RDN, 0x80000000, 0),              # ... but -0 in RDN
    (ADD, 0x80000000, 0x80000000, 0, RNE, 0x80000000, 0),
    (ADD, PINF, 0xFF800000, 0, RNE, QNAN, NV),
    (MUL, MAX_FINITE, TWO, 0, RNE, PINF, OF | NX),
    (MUL, MAX_FINITE, TWO, 0, RTZ, MAX_FINITE, OF | NX),
    (MUL, MAX_FINITE | SIGN, TWO, 0, RUP, MAX_FINITE | SIGN, OF | NX),
    (MUL, MIN_NORM, HALF, 0, RNE, 0x00400000, 0),        # exact subnormal: no UF
    (MUL, 0x00800001, HALF, 0, RNE, 0x00400000, UF | NX),
    (MUL, 0x3F7FFFFF, MIN_NORM, 0, RNE, MIN_NORM, UF | NX),  # rounds up to min normal, still tiny
    (MUL, 0x3F7FFFFF, MIN_NORM, 0, RTZ, 0x007FFFFF, UF | NX),
    (MUL, PINF, 0, 0, RNE, QNAN, NV),
    (DIV, ONE, 0, 0, RNE, PINF, DZ),
    (DIV, ONE | SIGN, 0, 0, RNE, PINF | SIGN, DZ),
    (DIV, 0, 0, 0, RNE, QNAN, NV),
    (DIV, ONE, THREE, 0, RNE, 0x3EAAAAAB, NX),
    (DIV, ONE, THREE, 0, RTZ, 0x3EAAAAAA, NX),
    (DIV, ONE, PINF, 0, RNE, 0, 0),
    (SQRT, 0x40800000, 0, 0, RNE, TWO, 0),               # sqrt(4)
    (SQRT, TWO, 0, 0, RNE, 0x3FB504F3, NX),              # sqrt(2)
    (SQRT, TWO, 0, 0, RUP, 0x3FB504F4, NX),
    (SQRT, ONE | SIGN, 0, 0, RNE, QNAN, NV),
    (SQRT, 0x80000000, 0, 0, RNE, 0x80000000, 0),        # sqrt(-0) = -0
    (SQRT, SNAN, 0, 0, RNE, QNAN, NV),
    (MADD, TWO, THREE, ONE, RNE, 0x40E00000, 0),         # 2*3 + 1 = 7
    (MSUB, TWO, THREE, ONE, RNE, 0x40A00000, 0),         # 2*3 - 1 = 5
    (NMSUB, TWO, THREE, ONE, RNE, 0xC0A00000, 0),        # -(2*3) + 1 = -5
    (NMADD, TWO, THREE, ONE, RNE, 0xC0E00000, 0),        # -(2*3) - 1 = -7
    (MADD, PINF, 0, QNAN, RNE, QNAN, NV),                # inf*0 + qNaN still raises NV
    (MADD, ONE, ONE, QNAN, RNE, QNAN, 0),
    (MADD, PINF, ONE, 0xFF800000, RNE, QNAN, NV),        # inf - inf
    # (1 + 2^-23)^2 - 1 = 2^-22 + 2^-46 with one rounding: a tie at 24 bits. NX shows the
    # product was not rounded first (that would give exactly 2^-22 with no flags).
    (MSUB, 0x3F800001, 0x3F800001, ONE, RNE, 0x34800000, NX),
    (MSUB, 0x3F800001, 0x3F800001, ONE, RUP, 0x34800001, NX),
    (SGNJ, ONE, TWO | SIGN, 0, RNE, ONE | SIGN, 0),
    (SGNJN, ONE, TWO | SIGN, 0, RNE, ONE, 0),
    (SGNJX, ONE | SIGN, TWO | SIGN, 0, RNE, ONE, 0),
    (SGNJ, SNAN, ONE, 0, RNE, SNAN, 0),                  # no NaN canonicalization, no flags
    (MIN, 0x80000000, 0, 0, RNE, 0x80000000, 0),
    (MAX, 0x80000000, 0, 0, RNE, 0, 0),
    (MIN, QNAN, ONE, 0, RNE, ONE, 0),
    (MAX, SNAN, ONE, 0, RNE, ONE, NV),
    (MIN, QNAN, SNAN, 0, RNE, QNAN, NV),
    (EQ, QNAN, ONE, 0, RNE, 0, 0),
    (EQ, SNAN, ONE, 0, RNE, 0, NV),
    (LT, QNAN, ONE, 0, RNE, 0, NV),
    (LE, ONE, ONE, 0, RNE, 1, 0),
    (LT, 0x80000000, 0, 0, RNE, 0, 0),
    (EQ, 0x80000000, 0, 0, RNE, 1, 0),
    (CLASS, 0xFF800000, 0, 0, RNE, 1 << 0, 0),
    (CLASS, 0, 0, 0, RNE, 1 << 4, 0),
    (CLASS, MIN_SUB, 0, 0, RNE, 1 << 5, 0),
    (CLASS, SNAN, 0, 0, RNE, 1 << 8, 0),
    (CLASS, QNAN, 0, 0, RNE, 1 << 9, 0),
    (CVT_W_S, 0x40200000, 0, 0, RNE, 2, NX),             # 2.5
    (CVT_W_S, 0x40200000, 0, 0, RMM, 3, NX),
    (CVT_W_S, 0xC0200000, 0, 0, RDN, 0xFFFFFFFD, NX),    # -2.5 -> -3
    (CVT_W_S, 0x4F000000, 0, 0, RNE, 0x7FFFFFFF, NV),    # 2^31 is out of range
    (CVT_W_S, 0xCF000000, 0, 0, RNE, 0x80000000, 0),     # -2^31 fits
    (CVT_W_S, QNAN, 0, 0, RNE, 0x7FFFFFFF, NV),
    (CVT_W_S, 0xFF800000, 0, 0, RNE, 0x80000000, NV),
    (CVT_WU_S, ONE | SIGN, 0, 0, RNE, 0, NV),
    (CVT_WU_S, 0xBE800000, 0, 0, RNE, 0, NX),            # -0.25 rounds to 0: no NV
    (CVT_WU_S, 0xBE800000, 0, 0, RDN, 0, NV),            # ... but RDN gives -1
    (CVT_WU_S, 0x4F800000, 0, 0, RNE, 0xFFFFFFFF, NV),   # 2^32
    (CVT_WU_S, QNAN, 0, 0, RNE, 0xFFFFFFFF, NV),
    (CVT_S_W, 0xFFFFFFFF, 0, 0, RNE, ONE | SIGN, 0),
    (CVT_S_W, 0x7FFFFFFF, 0, 0, RNE, 0x4F000000, NX),
    (CVT_S_W, 0x7FFFFFFF, 0, 0, RTZ, 0x4EFFFFFF, NX),
    (CVT_S_W, 16777217, 0, 0, RNE, 0x4B800000, NX),      # tie, stays even
    (CVT_S_WU, 0xFFFFFFFF, 0, 0, RNE, 0x4F800000, NX),
    (CVT_S_WU, 0, 0, 0, RNE, 0, 0),
    (MV_X_W, SNAN, 0, 0, RNE, SNAN, 0),
    (MV_W_X, 0xDEADBEEF, 0, 0, RNE, 0xDEADBEEF, 0),
]  # fmt: skip


def self_check():
    bad = 0
    for op, a, b, c, rm, want, want_flags in KNOWN_ANSWERS:
        got, flags = fpu_model(op, a, b, c, rm)
        if (got, flags) != (want, want_flags):
            bad += 1
            print(f"MODEL BUG: {OP_NAMES[op]} {a:#x} {b:#x} {c:#x} {RM_NAMES[rm]}: "
                  f"want {want:#010x} {describe_flags(want_flags)}, "
                  f"got {got:#010x} {describe_flags(flags)}")
    print(f"{len(KNOWN_ANSWERS) - bad}/{len(KNOWN_ANSWERS)} hand-worked answers match")
    return bad == 0


if __name__ == "__main__":
    import sys

    sys.exit(0 if self_check() else 1)
