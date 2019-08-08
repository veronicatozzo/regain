import warnings

import numpy as np
from sklearn.utils import check_array
from sklearn.utils.extmath import squared_norm
from sklearn.base import BaseEstimator

from regain.generalized_linear_model.base import GLM_GM, convergence
from regain.generalized_linear_model.base import build_adjacency_matrix
from regain.prox import soft_thresholding, soft_thresholding_od
from regain.norm import l1_od_norm


def loss(X, theta):
    n, d = X.shape
    objective = 0
    if not np.all(theta == theta.T):
        return np.float('inf')
    for r in range(d):
        selector = [i for i in range(d) if i != r]
        objective += objective_single_variable(X, theta[r, selector], n, r,
                                               selector, 0)
    return objective


def objective(X, theta, alpha):
    n, _ = X.shape
    objective = loss(X, theta)
    return objective + alpha*l1_od_norm(theta)


def objective_single_variable(X, theta, n, r, selector, alpha):
    objective = 0
    for i in range(X.shape[0]):
        XXT = X[i, r] * X[i, selector].dot(theta)
        expXT = np.exp(X[i, selector].dot(theta))
        objective += XXT - expXT
    return - (1/n) * objective + alpha*np.linalg.norm(theta, 1)


def fit_each_variable(X, ix, alpha=1e-2, gamma=1e-3, tol=1e-3,
                      max_iter=1000, verbose=0,
                      return_history=True, compute_objective=True,
                      return_n_iter=False, adjust_gamma=False):
    n, d = X.shape
    theta = np.zeros(d-1)
    selector = [i for i in range(d) if i != ix]

    def gradient(X, theta, r, selector, n):
        XTX = X[:, selector].T.dot(X[:, r])
        EXK = X[:, selector].T.dot(np.exp(X[:, selector].dot(theta)))
        return -(1/n)*(XTX - EXK)

    thetas = [theta]
    checks = []
    for iter_ in range(max_iter):
        theta_new = theta - gamma*gradient(X, theta, ix, selector, n)
        theta = soft_thresholding(theta_new, alpha*gamma)
        print(theta)
        thetas.append(theta)

        if iter_ > 0:
            check = convergence(iter=iter_,
                                obj=objective_single_variable(X, theta, n, ix,
                                                              selector, alpha),
                                iter_norm=np.linalg.norm(thetas[-2]-thetas[-1]),
                                iter_r_norm=(np.linalg.norm(thetas[-2] -
                                                            thetas[-1]) /
                                             np.linalg.norm(thetas[-1])))
            checks.append(check)
            # if adjust_gamma: # TODO multiply or divide
            if verbose:
                print('Iter: %d, objective: %.4f, iter_norm %.4f' %
                      (check[0], check[1], check[2]))

            if np.abs(check[2]) < tol:
                break

    return_list = [thetas[-1]]
    if return_history:
        return_list.append(thetas)
        return_list.append(checks)
    if return_n_iter:
        return_list.append(iter_)

    return return_list


def _gradient_poisson(X, theta,  n, A=None, rho=1, T=0):
    n, d = X.shape
    theta_new = np.zeros_like(theta)

    def gradient(X, theta, r, selector, n, A=None, rho=1, T=0):
        XTX = X[:, selector].T.dot(X[:, r])
        EXK = X[:, selector].T.dot(np.exp(X[:, selector].dot(theta)))
        if A is not None:
            to_add = (rho*T/n)*(theta[r, selector] - A[r, selector])
        else:
            to_add = 0
        return -(1/n)*(XTX - EXK + to_add)

    for ix in range(theta.shape[0]):
        selector = [i for i in range(d) if i != ix]
        theta_new[ix, selector] = gradient(
                                    X, theta[ix, selector], ix, selector, n,
                                    A, rho, T)
    theta_new = (theta_new + theta_new.T)/2
    return theta_new


def _fit(X, alpha=1e-2, gamma=1e-3, tol=1e-3, max_iter=1000, verbose=0,
         return_history=True, compute_objective=True, warm_start=None,
         return_n_iter=False, adjust_gamma=False, A=None, T=0, rho=1):
    n, d = X.shape
    if warm_start is None:
        theta = np.zeros((d, d))
    else:
        theta = check_array(warm_start)

    thetas = [theta]
    theta_new = theta.copy()
    checks = []
    for iter_ in range(max_iter):

        theta_new = theta - gamma*_gradient_poisson(X, theta,  n, A, rho, T)
        theta = (theta_new + theta_new.T)/2
        theta = soft_thresholding_od(theta, alpha*gamma)
        thetas.append(theta)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            check = convergence(iter=iter_,
                                obj=objective(X, theta, alpha),
                                iter_norm=np.linalg.norm(thetas[-2]-thetas[-1]),
                                iter_r_norm=(np.linalg.norm(thetas[-2] -
                                                            thetas[-1]) /
                                             np.linalg.norm(thetas[-1])))
        checks.append(check)
        if verbose:
            print('Iter: %d, objective: %.4f, iter_norm %.4f' %
                  (check[0], check[1], check[2]))

        if np.abs(check[2]) < tol:
            break

    return_list = [thetas[-1]]
    if return_history:
        return_list.append(thetas)
        return_list.append(checks)
    if return_n_iter:
        return_list.append(iter_)

    return return_list


class PoissonGraphicalModel(GLM_GM, BaseEstimator):

    def __init__(self, alpha=0.01, tol=1e-4, rtol=1e-4, reconstruction='union',
                 mode='coordinate_descent', max_iter=100,
                 verbose=False, return_history=True, return_n_iter=False,
                 compute_objective=True):
        super(PoissonGraphicalModel, self).__init__(
            alpha, tol, rtol, max_iter, verbose, return_history, return_n_iter,
            compute_objective)
        self.reconstruction = reconstruction
        self.mode = mode

    def get_precision(self):
        return self.precision_

    def fit(self, X, y=None, gamma=0.1):
        """
        X : ndarray, shape = (n_samples * n_times, n_dimensions)
            Data matrix.
        y : added for compatiblity
        gamma: float,
            Step size of the proximal gradient descent.
        """
        X = check_array(X)
        if self.mode.lower() == 'symmetric_fbs':
            res = _fit(X, self.alpha, tol=self.tol, gamma=gamma,
                       max_iter=self.max_iter,
                       verbose=self.verbose)
            self.precision_ = res[0]
            self.history = res[1:]
        elif self.mode.lower() == 'coordinate_descent':
            thetas_pred = []
            historys = []
            for ix in range(X.shape[1]):
                verbose = min(0, self.verbose-1)
                res = fit_each_variable(X, ix, self.alpha, tol=self.tol,
                                        gamma=gamma,
                                        verbose=verbose)
                thetas_pred.append(res[0])
                historys.append(res[1:])
            self.precision_ = build_adjacency_matrix(thetas_pred,
                                                     how=self.reconstruction)
            self.history = historys
        else:
            raise ValueError('Unknown optimization mode. Found ' + self.mode +
                             ". Options are 'coordiante_descent', "
                             "'symmetric_fbs'")
        return self

    def score(self, X, y=None):
        return 0
