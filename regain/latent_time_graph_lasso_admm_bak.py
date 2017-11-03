"""Graphical latent variable models selection over time via ADMM."""
from __future__ import division

import numpy as np
import warnings

from functools import partial
from six.moves import range
from sklearn.covariance import empirical_covariance
from sklearn.utils.extmath import fast_logdet, squared_norm

from regain.norm import l1_od_norm
from regain.prox import prox_logdet_alt, prox_laplacian, soft_thresholding_sign
from regain.prox import prox_trace_indicator
from regain.time_graph_lasso_admm import log_likelihood
from regain.utils import convergence


def objective(n_samples, S, R, Z_0, Z_1, Z_2, W_0, W_1, W_2,
              alpha, tau, beta, eta, psi, phi):
    """Objective function for time-varying graphical lasso."""
    obj = np.sum(-n * log_likelihood(s, r) for s, r, n in zip(S, R, n_samples))
    obj += alpha * np.sum(map(l1_od_norm, Z_0))
    obj += tau * np.sum(map(partial(np.linalg.norm, ord='nuc'), W_0))
    obj += beta * np.sum(psi(z2 - z1) for z1, z2 in zip(Z_1, Z_2))
    obj += eta * np.sum(phi(w2 - w1) for w1, w2 in zip(W_1, W_2))
    return obj


def time_latent_graph_lasso(
        data_list, alpha=1, tau=1, rho=1, beta=1., eta=1., max_iter=1000,
        verbose=False,
        tol=1e-4, rtol=1e-2, return_history=False):
    r"""Time-varying latent variable graphical lasso solver.

    Solves the following problem via ADMM:
        min sum_{i=1}^T -n_i log_likelihood(K_i-L_i) + alpha ||K_i||_{od,1}
            + tau ||L_i||_*
            + beta sum_{i=2}^T Psi(K_i - K_{i-1})
            + eta sum_{i=2}^T Phi(L_i - L_{i-1})

    where S is the empirical covariance of the data
    matrix D (training observations by features).

    Parameters
    ----------
    data_list : list of 2-dimensional matrices.
        Input matrices.
    alpha, tau : float, optional
        Regularisation parameters.
    rho : float, optional
        Augmented Lagrangian parameter.
    max_iter : int, optional
        Maximum number of iterations.
    tol : float, optional
        Absolute tolerance for convergence.
    rtol : float, optional
        Relative tolerance for convergence.
    return_history : bool, optional
        Return the history of computed values.

    Returns
    -------
    K, L : numpy.array, 3-dimensional (T x d x d)
        Solution to the problem for each time t=1...T .
    history : list
        If return_history, then also a structure that contains the
        objective value, the primal and dual residual norms, and tolerances
        for the primal and dual residual norms at each iteration.
    """
    S = np.array(map(empirical_covariance, data_list))
    n_samples = np.array([s.shape[0] for s in data_list])

    K = np.zeros_like(S)
    L = np.zeros_like(S)
    X = np.zeros_like(S)
    # Z_0 = np.zeros_like(K)
    # Z_1 = np.zeros_like(K)[:-1]
    # Z_2 = np.zeros_like(K)[1:]
    # W_0 = np.zeros_like(K)
    # W_1 = np.zeros_like(K)[:-1]
    # W_2 = np.zeros_like(K)[1:]
    U_0 = np.zeros_like(S)
    U_1 = np.zeros_like(S)[:-1]
    U_2 = np.zeros_like(S)[1:]
    Y_0 = np.zeros_like(S)
    Y_1 = np.zeros_like(S)[:-1]
    Y_2 = np.zeros_like(S)[1:]

    U_consensus = np.zeros_like(S)
    Y_consensus = np.zeros_like(S)
    Z_consensus = np.zeros_like(S)
    Z_consensus_old = np.zeros_like(S)
    W_consensus = np.zeros_like(S)
    W_consensus_old = np.zeros_like(S)

    # divisor for consensus variables, accounting for two less matrices
    divisor = np.zeros(S.shape[0]) + 3
    divisor[0] -= 1
    divisor[-1] -= 1
    # eta = np.divide(n_samples, divisor * rho)

    checks = []
    for _ in range(max_iter):
        # update R
        # A = Z_consensus - U_consensus
        A = K - L - X
        A += np.array(map(np.transpose, A))
        A /= 2.
        A *= - rho / n_samples[:, np.newaxis, np.newaxis]
        A += S
        R = np.array(map(prox_logdet_alt, A, n_samples / rho))

        # update Z_0
        # Zold = Z
        # X_hat = alpha * X + (1 - alpha) * Zold
        soft_thresholding = partial(soft_thresholding_sign, lamda=alpha / rho)
        Z_0 = np.array(map(soft_thresholding, K + U_0))

        # update Z_1, Z_2
        # prox_l = partial(prox_laplacian, beta=2. * beta / rho)
        # prox_e = np.array(map(prox_l, K[1:] - K[:-1] + U_2 - U_1))
        prox_e = prox_laplacian(K[1:] - K[:-1] + U_2 - U_1,
                                beta=2. * beta / rho)
        Z_1 = .5 * (K[:-1] + K[1:] + U_1 + U_2 - prox_e)
        Z_2 = .5 * (K[:-1] + K[1:] + U_1 + U_2 + prox_e)

        # update W_0
        A = L + Y_0
        W_0 = np.array(map(prox_trace_indicator, A, n_samples * tau / rho))

        # update W_1, W_2
        prox_e = prox_laplacian(L[1:] - L[:-1] + Y_2 - Y_1,
                                beta=2. * eta / rho)
        W_1 = .5 * (L[:-1] + L[1:] + Y_1 + Y_2 - prox_e)
        W_2 = .5 * (L[:-1] + L[1:] + Y_1 + Y_2 + prox_e)

        # update K, L
        K = L + R - X + Z_0 - U_0
        K[:-1] += Z_1 - U_1
        K[1:] += Z_2 - U_2
        K /= divisor[:, np.newaxis, np.newaxis] + 1

        L = R - K - X + W_0 - Y_0
        L[:-1] += W_1 - Y_1
        L[1:] += W_2 - Y_2
        L /= divisor[:, np.newaxis, np.newaxis] - 1


        # update residuals
        X += K - L - R
        U_0 += (K - Z_0)
        U_1 += (K[:-1] - Z_1)
        U_2 += (K[1:] - Z_2)
        Y_0 += (L - W_0)
        Y_1 += (L[:-1] - W_1)
        Y_2 += (L[1:] - W_2)

        # diagnostics, reporting, termination checks
        Z_consensus = Z_0.copy()
        Z_consensus[:-1] += Z_1
        Z_consensus[1:] += Z_2

        U_consensus = U_0.copy()
        U_consensus[:-1] += U_1
        U_consensus[1:] += U_2

        W_consensus = W_0.copy()
        W_consensus[:-1] += W_1
        W_consensus[1:] += W_2

        Y_consensus = Y_0.copy()
        Y_consensus[:-1] += Y_1
        Y_consensus[1:] += Y_2

        np.divide(Z_consensus, divisor[:, np.newaxis, np.newaxis], out=Z_consensus)
        np.divide(U_consensus, divisor[:, np.newaxis, np.newaxis], out=U_consensus)

        check = convergence(
            obj=objective(n_samples, S, R, Z_0, Z_1, Z_2, W_0, W_1, W_2,
                          alpha, tau, beta, eta, squared_norm, squared_norm),
            rnorm=np.sqrt(squared_norm(K - Z_consensus) +
                          squared_norm(L - W_consensus)),
            snorm=np.sqrt(squared_norm(rho * (Z_consensus - Z_consensus_old)) +
                          squared_norm(rho * (W_consensus - W_consensus_old))),
            e_pri=np.sqrt(np.prod(K.shape) * 2) * tol + rtol * max(
                np.sqrt(squared_norm(K) + squared_norm(L)),
                np.sqrt(squared_norm(Z_consensus) + squared_norm(W_consensus))),
            e_dual=np.sqrt(np.prod(K.shape) * 2) * tol +
                rtol * np.sqrt(squared_norm(rho * (U_consensus)) +
                               squared_norm(rho * (Y_consensus)))
        )
        Z_consensus_old = Z_consensus.copy()
        W_consensus_old = W_consensus.copy()

        if verbose:
            print("obj: %.4f, rnorm: %.4f, snorm: %.4f,"
                  "eps_pri: %.4f, eps_dual: %.4f" % check)

        checks.append(check)
        if check.rnorm <= check.e_pri and check.snorm <= check.e_dual:
            break
    else:
        warnings.warn("Objective did not converge.")

    if return_history:
        return K, L, S, checks
    return K, L, S