# N-2 resolution method for multicomponent FCI
import numpy
import scipy
from pyscf import lib
from pyscf import ao2mo
from pyscf.fci import cistring, selected_ci
from pyscf import neo
from pyscf.neo.fci_n_resolution import make_hdiag
from pyscf.neo.fci_n_resolution import FCI as _FCI
from pyscf.fci.fci_uhf_slow_n_minus_2_resolution import gen_des_des_str_index
import time

def contract(h1, h2, fcivec, norb, nparticle, dd_index=None, d_index=None):
    ndim = len(norb)
    if dd_index is None:
        dd_index = []
        for i in range(ndim):
            if nparticle[i] > 1:
                dd_index.append(gen_des_des_str_index(range(norb[i]), nparticle[i]))
            else:
                dd_index.append(None)
    if d_index is None:
        d_index = []
        for i in range(ndim):
            if nparticle[i] > 0:
                try:
                    d_index_ = cistring.gen_des_str_index(range(norb[i]), nparticle[i])
                except NotImplementedError:
                    if nparticle[i] == 1:
                        d_index_ = numpy.zeros((norb[i], 1, 4), dtype=numpy.int32)
                        d_index_[:,:,-1] = 1
                        d_index_[:,:,1] = numpy.arange(norb[i], dtype=numpy.int32).reshape(-1,1)
                    else:
                        raise NotImplementedError('64 orbitals or more and not 1 occupation')
                d_index.append(d_index_)
            else:
                d_index.append(None)
    ndim = len(norb)
    dim = []
    for i in range(ndim):
        dim.append(cistring.num_strings(norb[i], nparticle[i]))
    ci0 = fcivec.reshape(dim)

    fcinew = numpy.zeros_like(ci0, dtype=fcivec.dtype)

    t1_cache = []
    for k in range(ndim):
        if dd_index[k] is not None:
            assert h2[k][k] is not None
            h2_ = ao2mo.restore(1, h2[k][k], norb[k])
            m = len(dd_index[k])
            dim2 = dim.copy()
            dim2[k] = m
            t1 = numpy.zeros((norb[k],norb[k]) + tuple(dim2), dtype=fcivec.dtype)
            str0_indices = [slice(None)] * ndim
            str1_indices = [slice(None)] * ndim
            for str1, tab in enumerate(dd_index[k]):
                str1_indices[k] = str1
                str1_indices_tuple = tuple(str1_indices)
                for i, j, str0, sign in tab:
                    str0_indices[k] = str0
                    t1[(i,j)+str1_indices_tuple] += sign * ci0[tuple(str0_indices)]

            g1 = lib.einsum('pqrs,qsA->prA', h2_.reshape([norb[k]]*4),
                            t1.reshape([norb[k]]*2+[-1]))
            g1 = g1.reshape([norb[k]]*2+dim2)
            t1 = None

            for str1, tab in enumerate(dd_index[k]):
                str1_indices[k] = str1
                str1_indices_tuple = tuple(str1_indices)
                for i, j, str0, sign in tab:
                    str0_indices[k] = str0
                    fcinew[tuple(str0_indices)] += sign * g1[(i,j)+str1_indices_tuple]
            g1 = None

        if d_index[k] is not None:
            assert h1[k] is not None
            m = cistring.num_strings(norb[k], nparticle[k]-1)
            dim2 = dim.copy()
            dim2[k] = m
            t1 = numpy.zeros((norb[k],) + tuple(dim2), dtype=fcivec.dtype)
            str0_indices = [slice(None)] * ndim
            str1_indices = [slice(None)] * ndim
            for str0, tab in enumerate(d_index[k]):
                str0_indices[k] = str0
                str0_indices_tuple = tuple(str0_indices)
                for _, i, str1, sign in tab:
                    str1_indices[k] = str1
                    t1[(i,)+tuple(str1_indices)] += sign * ci0[str0_indices_tuple]
            t1_cache.append(t1)

            g1 = lib.einsum('pq,qA->pA', h1[k], t1.reshape((norb[k], -1)))
            g1 = g1.reshape([norb[k]]+dim2)

            for str0, tab in enumerate(d_index[k]):
                str0_indices[k] = str0
                str0_indices_tuple = tuple(str0_indices)
                for _, i, str1, sign in tab:
                    str1_indices[k] = str1
                    fcinew[str0_indices_tuple] += sign * g1[(i,)+tuple(str1_indices)]
            g1 = None
        else:
            t1_cache.append(None)

    done = [[False] * ndim for _ in range(ndim)]
    for k in range(ndim):
        for l in range(k+1, ndim):
            if (h2[k][l] is not None or h2[l][k] is not None) and d_index[k] is not None \
                and d_index[l] is not None and not done[k][l]:
                m1 = cistring.num_strings(norb[k], nparticle[k]-1)
                m2 = cistring.num_strings(norb[l], nparticle[l]-1)
                dim2 = dim.copy()
                dim2[k] = m1
                dim2[l] = m2
                t1 = numpy.zeros((norb[k],norb[l]) + tuple(dim2), dtype=fcivec.dtype)

                t1_k = t1_cache[k]
                str0_indices = [slice(None)] * (ndim + 1)
                str1_indices = [slice(None)] * (ndim + 2)
                for str0, tab in enumerate(d_index[l]):
                    str0_indices[l + 1] = str0
                    str0_indices_tuple = tuple(str0_indices)
                    for _, i, str1, sign in tab:
                        str1_indices[1] = i
                        str1_indices[l + 2] = str1
                        t1[tuple(str1_indices)] += sign * t1_k[str0_indices_tuple]

                if h2[k][l] is not None:
                    g1 = lib.einsum('pqrs,qsA->prA', h2[k][l].reshape([norb[k]]*2+[norb[l]]*2),
                                    t1.reshape((norb[k], norb[l], -1)))
                else:
                    g1 = lib.einsum('rspq,qsA->prA', h2[l][k].reshape([norb[l]]*2+[norb[k]]*2),
                                    t1.reshape((norb[k], norb[l], -1)))
                g1 = g1.reshape([norb[k], norb[l]]+dim2)
                dim3 = dim.copy()
                dim3[l] = m2
                t1 = numpy.zeros((norb[l],) + tuple(dim3), dtype=fcivec.dtype)

                str0_indices = [slice(None)] * (ndim + 1)
                str1_indices = [slice(None)] * (ndim + 2)
                for str0, tab in enumerate(d_index[k]):
                    str0_indices[k + 1] = str0
                    str0_indices_tuple = tuple(str0_indices)
                    for _, i, str1, sign in tab:
                        str1_indices[0] = i
                        str1_indices[k + 2] = str1
                        t1[str0_indices_tuple] += sign * g1[tuple(str1_indices)]
                g1 = None
                str0_indices = [slice(None)] * ndim
                str1_indices = [slice(None)] * (ndim + 1)
                for str0, tab in enumerate(d_index[l]):
                    str0_indices[l] = str0
                    str0_indices_tuple = tuple(str0_indices)
                    for _, i, str1, sign in tab:
                        str1_indices[0] = i
                        str1_indices[l + 1] = str1
                        fcinew[str0_indices_tuple] += sign * t1[tuple(str1_indices)]
                done[k][l] = done[l][k] = True
    return fcinew.reshape(fcivec.shape)

def kernel(h1, g2, norb, nparticle, ecore=0, ci0=None, hdiag=None, nroots=1):
    h2 = [[None] * len(norb) for _ in range(len(norb))]
    for i in range(len(norb)):
        for j in range(len(norb)):
            if g2[i][j] is not None:
                if i == j:
                    h2[i][j] = g2[i][j] * 0.5
                else:
                    h2[i][j] = g2[i][j]

    def hop(c):
        hc = contract(h1, h2, c, norb, nparticle)
        return hc.reshape(-1)
    if hdiag is None:
        hdiag = make_hdiag(h1, g2, norb, nparticle)
    if ci0 is None:
        print('N-2 resolution method')
        dim = []
        for i in range(len(norb)):
            dim.append(cistring.num_strings(norb[i], nparticle[i]))
        print(f'FCI vector shape: {dim}', flush=True)
        print(f'FCI dimension: {hdiag.size}', flush=True)
        addrs = numpy.argpartition(hdiag, nroots-1)[:nroots]
        ci0 = []
        for addr in addrs:
            print(f'{hdiag[addr]=}', flush=True)
            ci0_ = numpy.zeros(hdiag.size)
            ci0_[addr] = 1
            ci0.append(ci0_)

    precond = lambda x, e, *args: x/(hdiag-e+1e-4)
    t0 = time.time()
    converged, e, c = lib.davidson1(lambda xs: [hop(x) for x in xs],
                                    ci0, precond, max_cycle=100,
                                    max_memory=256000, nroots=nroots, verbose=10)
    print(f'davidson: {time.time() - t0} seconds', flush=True)
    if converged[0]:
        print('FCI Davidson converged!')
    else:
        print('FCI Davidson did not converge according to current setting.')
    return e+ecore, c

def energy(h1, g2, fcivec, norb, nparticle, ecore=0):
    h2 = [[None] * len(norb) for _ in range(len(norb))]
    for i in range(len(norb)):
        for j in range(len(norb)):
            if g2[i][j] is not None:
                if i == j:
                    h2[i][j] = g2[i][j] * 0.5
                else:
                    h2[i][j] = g2[i][j]
    ci1 = contract(h1, h2, fcivec, norb, nparticle)
    return numpy.dot(fcivec, ci1) + ecore

def integrals(mf):
    from functools import reduce
    mol = mf.mol
    nelec = mol.elec.nelec
    h1e_a = None
    if nelec[0] > 0:
        h1e_a = reduce(numpy.dot, (mf.mf_elec.mo_coeff[0].T, mf.mf_elec.hcore_static, mf.mf_elec.mo_coeff[0]))
    h1e_b = None
    if nelec[1] > 0:
        h1e_b = reduce(numpy.dot, (mf.mf_elec.mo_coeff[1].T, mf.mf_elec.hcore_static, mf.mf_elec.mo_coeff[1]))
    h1 = [h1e_a, h1e_b]
    for i in range(mol.nuc_num):
        h1n = reduce(numpy.dot, (mf.mf_nuc[i].mo_coeff.T, mf.mf_nuc[i].hcore_static, mf.mf_nuc[i].mo_coeff))
        h1.append(h1n)

    g2 = [[None] * (2+mol.nuc_num) for _ in range(2+mol.nuc_num)]
    if nelec[0] > 1:
        eri_ee_aa = ao2mo.kernel(mf.mf_elec._eri,
                                 (mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0],
                                  mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0]),
                                 compact=False)
        g2[0][0] = eri_ee_aa
    if nelec[1] > 1:
        eri_ee_bb = ao2mo.kernel(mf.mf_elec._eri,
                                 (mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1],
                                  mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                                 compact=False)
        g2[1][1] = eri_ee_bb
    if nelec[0] > 0 and nelec[1] > 0:
        eri_ee_ab = ao2mo.kernel(mf.mf_elec._eri,
                                 (mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0],
                                  mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                                 compact=False)
        g2[0][1] = eri_ee_ab

    for i in range(mol.nuc_num):
        ia = mol.nuc[i].atom_index
        charge = mol.atom_charge(ia)
        if nelec[0] > 0:
            eri_ne = -charge * ao2mo.kernel(mf._eri_ne[i],
                                            (mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff,
                                             mf.mf_elec.mo_coeff[0], mf.mf_elec.mo_coeff[0]),
                                            compact=False)
            g2[i+2][0] = eri_ne
        if nelec[1] > 0:
            eri_ne = -charge * ao2mo.kernel(mf._eri_ne[i],
                                            (mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff,
                                             mf.mf_elec.mo_coeff[1], mf.mf_elec.mo_coeff[1]),
                                            compact=False)
            g2[i+2][1] = eri_ne

    for i in range(mol.nuc_num):
        ia = mol.nuc[i].atom_index
        charge_i = mol.atom_charge(ia)
        for j in range(i):
            ja = mol.nuc[j].atom_index
            charge = charge_i * mol.atom_charge(ja)
            eri_nn = charge * ao2mo.kernel(mf._eri_nn[j][i],
                                           (mf.mf_nuc[j].mo_coeff, mf.mf_nuc[j].mo_coeff,
                                            mf.mf_nuc[i].mo_coeff, mf.mf_nuc[i].mo_coeff),
                                           compact=False)
            g2[j+2][i+2] = eri_nn
    return h1, g2

def FCI(mf, kernel=kernel, integrals=integrals, energy=energy):
    return _FCI(mf, kernel=kernel, integrals=integrals, energy=energy)


if __name__ == '__main__':
    mol = neo.M(atom='H 0 0 0; H 0 1.0 0; H 1.0 0 0; He 1.0 1.0 0', basis='6-31G',
                nuc_basis='1s1p1d', charge=0, spin=1)
    mol.verbose = 0
    mol.output = None

    mf = neo.HF(mol, unrestricted=True)
    mf.conv_tol_grad = 1e-7
    mf.kernel()
    print(f'HF energy: {mf.e_tot}', flush=True)
    t0 = time.time()
    e0 = _FCI(mf).kernel()[0]
    print(f'N resolution FCI energy: {e0}, time: {time.time() - t0} s')
    t0 = time.time()
    e1 = FCI(mf).kernel()[0]
    print(f'N-2 resolution FCI energy: {e1}, difference: {e1 - e0}, time: {time.time() - t0} s')
