import torch
import numpy as np
import math
from types import SimpleNamespace
from ...utils.algebra_utils import double_factorial


def atomic_orbital_norm(basis: SimpleNamespace) -> torch.Tensor:
    """Computes the norm of the atomic orbitals

    Args:
        basis (Namespace): basis object of the Molecule instance

    Returns:
        torch.tensor: Norm of the atomic orbitals

    Examples::
        >>> mol = Molecule('h2.xyz', basis='dzp', calculator='adf')
        >>> norm = atomic_orbital_norm(mol.basis)
    """

    # spherical
    if basis.harmonics_type == "sph":
        if basis.radial_type.startswith("sto"):
            return norm_slater_spherical(basis.bas_n, basis.bas_exp)

        elif basis.radial_type.startswith("gto"):
            return norm_gaussian_spherical(basis.bas_n, basis.bas_exp)

        else:
            raise ValueError("%s is not a valid radial_type")

    # cartesian
    elif basis.harmonics_type == "cart":
        if basis.radial_type.startswith("sto"):
            return norm_slater_cartesian(
                basis.bas_kx, basis.bas_ky, basis.bas_kz, basis.bas_kr, basis.bas_exp
            )

        elif basis.radial_type.startswith("gto"):
            return norm_gaussian_cartesian(
                basis.bas_kx, basis.bas_ky, basis.bas_kz, basis.bas_exp
            )

        else:
            raise ValueError("%s is not a valid radial_type")


def norm_slater_spherical(bas_n: torch.Tensor, bas_exp: torch.Tensor) -> torch.Tensor:
    """Normalization of STOs with Spherical Harmonics.

    References:
        * www.theochem.ru.nl/~pwormer/Knowino/knowino.org/wiki/Slater_orbital
        * C Filippi, JCP 105, 213 1996
        * Monte Carlo Methods in Ab Initio Quantum Chemistry, B.L. Hammond

    Args:
        bas_n (torch.Tensor): Principal quantum number
        bas_exp (torch.Tensor): Slater exponents

    Returns:
        torch.Tensor: Normalization factor
    """
    nfact = torch.as_tensor(
        [math.factorial(2 * n) for n in bas_n], dtype=torch.get_default_dtype()
    )
    return (2 * bas_exp) ** bas_n * torch.sqrt(2 * bas_exp / nfact)


def norm_gaussian_spherical(bas_n: torch.Tensor, bas_exp: torch.Tensor) -> torch.Tensor:
    """Normlization of GTOs with spherical harmonics. \n
     * Computational Quantum Chemistry: An interactive Intrduction to basis set theory \n
        eq : 1.14 page 23.

    Args:
        bas_n (torch.tensor): prinicpal quantum number
        bas_exp (torch.tensor): slater exponents

    Returns:
        torch.tensor: normalization factor
    """
    bas_n = torch.tensor(bas_n)
    bas_n = bas_n + 1.0
    exp1 = 0.25 * (2.0 * bas_n + 1.0)

    A = torch.tensor(bas_exp) ** exp1
    B = 2 ** (2.0 * bas_n + 3.0 / 2)
    C = torch.as_tensor(double_factorial(2 * bas_n.int() - 1) * np.pi**0.5).type(
        torch.get_default_dtype()
    )

    return torch.sqrt(B / C) * A


def norm_slater_cartesian(
    a: torch.Tensor,
    b: torch.Tensor,
    c: torch.Tensor,
    n: torch.Tensor,
    exp: torch.Tensor,
) -> torch.Tensor:
    """Normaliation of STos with cartesian harmonics. \n
     * Monte Carlo Methods in Ab Initio Quantum Chemistry page 279

    Args:
        a (torch.tensor): exponent of x
        b (torch.tensor): exponent of y
        c (torch.tensor): exponent of z
        n (torch.tensor): exponent of r
        exp (torch.tensor): Sater exponent

    Returns:
        torch.tensor: normalization factor
    """
    lvals = a + b + c + n + 1.0

    lfact = torch.as_tensor([math.factorial(int(2 * i)) for i in lvals]).type(
        torch.get_default_dtype()
    )

    prefact = 4 * np.pi * lfact / ((2 * exp) ** (2 * lvals + 1))

    num = torch.as_tensor(
        double_factorial(2 * a.astype("int") - 1)
        * double_factorial(2 * b.astype("int") - 1)
        * double_factorial(2 * c.astype("int") - 1)
    ).type(torch.get_default_dtype())

    denom = torch.as_tensor(
        double_factorial((2 * a + 2 * b + 2 * c + 1).astype("int"))
    ).type(torch.get_default_dtype())

    return torch.sqrt(1.0 / (prefact * num / denom))


def norm_gaussian_cartesian(
    a: torch.Tensor, b: torch.Tensor, c: torch.Tensor, exp: torch.Tensor
) -> torch.Tensor:
    """Normaliation of GTOs with cartesian harmonics. \n
     * Monte Carlo Methods in Ab Initio Quantum Chemistry page 279

    Args:
        a (torch.tensor): exponent of x
        b (torch.tensor): exponent of y
        c (torch.tensor): exponent of z
        exp (torch.tensor): Slater exponent

    Returns:
        torch.tensor: normalization factor
    """
    pref = torch.as_tensor((2 * exp / np.pi) ** (0.75))
    am1 = (2 * a - 1).astype("int")
    x = (4 * exp) ** (a / 2) / torch.sqrt(torch.as_tensor(double_factorial(am1)))

    bm1 = (2 * b - 1).astype("int")
    y = (4 * exp) ** (b / 2) / torch.sqrt(torch.as_tensor(double_factorial(bm1)))

    cm1 = (2 * c - 1).astype("int")
    z = (4 * exp) ** (c / 2) / torch.sqrt(torch.as_tensor(double_factorial(cm1)))

    return (pref * x * y * z).type(torch.get_default_dtype())
