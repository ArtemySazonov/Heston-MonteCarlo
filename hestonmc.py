import numpy as np

from math import erf, sqrt, exp, log
sqrt2 = 1/sqrt(2)

from scipy.stats import norm

from typing import Union, Callable, Optional
from copy import error
from scipy.interpolate import RectBivariateSpline
from scipy.optimize import newton, root_scalar

from numba import jit, njit, prange, float64
from numba.experimental import jitclass
from numba_stats import norm, uniform

@njit
def Phi(x):
    return (0.5 + 0.5 * erf(x * sqrt2))

if __name__ == '__main__':
    print("This is a module. Please import it.\n")
    exit(-1)

@jitclass([("kappa", float64),
           ("gamma", float64),
           ("rho", float64), 
           ("vbar", float64),
           ("v0", float64)])
class HestonParameters:
    def __init__(self, kappa, gamma, rho, vbar, v0):
        self.kappa = kappa
        self.gamma = gamma
        self.rho = rho
        self.vbar = vbar
        self.v0 = v0
        
@jitclass([("stock_price", float64),
           ("interest_rate", float64)])
class MarketState:
    def __init__(self, stock_price, interest_rate):
        self.stock_price = stock_price
        self.interest_rate = interest_rate

def get_len_conf_interval(data:             np.ndarray, 
                          confidence_level: float = 0.05):
    """Get the confidence interval length for a given confidence level.
    Args:
        data:             The data to compute the confidence interval for.
        confidence_level: The confidence level to use.
    
    Returns:
        The confidence interval.
    """
    return -2*norm.ppf(confidence_level*0.5) * sqrt(np.var(data) / len(data))

@njit
def set_seed(value):
    np.random.seed(value)

def mc_price(payoff:                 Callable,
             simulate:               Callable,
             state:                  MarketState,
             heston_params:          HestonParameters,
             T:                      float    = 1.,
             N_T:                    int      = 100,
             absolute_error:         float    = 0.01,
             confidence_level:       float    = 0.05,
             batch_size:             int      = 10_000,
             MAX_ITER:               int      = 100_000,
             control_variate_payoff: Callable = None,
             control_variate_iter:   int      = 1_000,
             mu:                     float    = None,
             verbose:                bool     = False,
             random_seed:            int      = None,
             **kwargs):
    """A function that performs a Monte-Carlo based pricing of a derivative with a given payoff (possibly path-dependent) under the Heston model.
    Args:
        payoff (Callable):                           Payoff function
        simulate (Callable):                         Simulation engine
        state (MarketState):                         Market state
        heston_params (HestonParameters):            Heston parameters
        T (float, optional):                         Contract expiration T. Defaults to 1.. 
        N_T (int, optional):                         Number of steps in time. Defaults to 100.
        absolute_error (float, optional):            Absolute error of the price. Defaults to 0.01 (corresponds to 1 cent). 
        confidence_level (float, optional):          Confidence level for the price. Defaults to 0.05.
        batch_size (int, optional):                  Path-batch size. Defaults to 10_000.
        MAX_ITER (int, optional):                    Maximum number of iterations. Defaults to 100_000.  
        control_variate_payoff (Callable, optional): Control variate payoff. Defaults to None.
        control_variate_iter (int, optional):        Number of iterations for the control variate. Defaults to 1_000.
        verbose (bool, optional):                    Verbose flag. If true, the technical information is printed. Defaults to False.
        random_seed (int, optional):                 Random seed. Defaults to None.
        **kwargs:                                    Additional arguments for the simulation engine.
    Returns:    
        The price(-s) of the derivative(-s).    
    """

    arg = {'state':         state,
           'heston_params': heston_params, 
           'T':             T, 
           'N_T':           N_T, 
           'n_simulations': batch_size}

    args       = {**arg, **kwargs}
    iter_count = 0   

    length_conf_interval = 1.
    n                    = 0
    C                    = -2*norm.ppf(confidence_level*0.5, loc = 0., scale = 1.)
    # print(confidence_level*0.5, -2*norm.ppf(confidence_level*0.5, loc = 0., scale = 1.))
    sigma_n              = 0.
    batch_new            = np.zeros(batch_size, dtype=np.float64)
    current_Pt_sum       = 0.        

    if random_seed is not None:
        set_seed(random_seed)

    if control_variate_payoff is None:
        while length_conf_interval > absolute_error and iter_count < MAX_ITER:
            temp  = simulate(**args)[0]
            batch_new = payoff(temp)

            iter_count+=1

            sigma_n = (sigma_n*(n-1.) + np.var(batch_new)*(4.*batch_size - 1.))/(n + 4.*batch_size - 1.)
            current_Pt_sum = current_Pt_sum + np.sum(batch_new) 

            n+=4*batch_size
            length_conf_interval = C * sqrt(sigma_n / n)
            # print(sigma_n, length_conf_interval, n, C, sigma_n / n)
    else:
        if mu == None:
            return "NaN"

        S = simulate(state = state, heston_params = heston_params, T = T, N_T = N_T, n_simulations = control_variate_iter)[0]
        s1 = payoff(S)
        s2 = control_variate_payoff(S)
        c = np.cov(s1, s2)
        theta = c[0, 1] / c[1, 1]
        while length_conf_interval > absolute_error and iter_count < MAX_ITER:
            temp  = simulate(**args)[0]
            batch_new = payoff(temp) - theta * (control_variate_payoff(temp) - mu)
            iter_count+=1

            sigma_n = (sigma_n*(n-1.) + np.var(batch_new)*(4*batch_size - 1.))/(n + 4*batch_size - 1.)
            current_Pt_sum = current_Pt_sum + np.sum(batch_new) 

            n+=4*batch_size
            length_conf_interval = C * np.sqrt(sigma_n / n)

    if verbose:
        if random_seed is not None:
            print(f"Random seed:                {random_seed}")

        if control_variate_payoff is not None:
            print(f"Control variate payoff:     {control_variate_payoff.__name__}")
            print(f"Control variate iterations: {control_variate_iter}")
        
        print(f"Number of simulate calls:   {iter_count}\nMAX_ITER:                   {MAX_ITER}\nNumber of paths:            {n}\nAbsolute error:             {absolute_error}\nLength of the conf intl:    {length_conf_interval}\nConfidence level:           {confidence_level}\n")

    return current_Pt_sum/n

@njit(parallel=True, cache=True, nogil=True)
def simulate_heston_euler(state:           MarketState,
                          heston_params:   HestonParameters,
                          T:               float = 1.,
                          N_T:             int   = 100,
                          n_simulations:   int   = 10_000
                          ) -> np.ndarray:
    """Simulation engine for the Heston model using the Euler scheme.
    Args:
        state (MarketState):              Market state.
        heston_params (HestonParameters): Parameters of the Heston model.
        T (float, optional):              Contract termination time expressed as a number of years. Defaults to 1..
        N_T (int, optional):              Number of steps in time. Defaults to 100.
        n_simulations (int, optional):    Number of simulations. Defaults to 10_000.
    Raises:
        error: Contract termination time must be positive.
    Returns:
        A tuple containing the simulated stock price and the simulated stochastic variance.
        The number of paths is doubled to account for the antithetic variates.
    """    
    if T <= 0:
        raise error("Contract termination time must be positive.")
    
    r, s0 = state.interest_rate, state.stock_price
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, heston_params.vbar, heston_params.gamma
    
    dt         = T/float(N_T)
    
    Z          = np.random.standard_normal(size=(2, n_simulations, N_T))
    V          = np.empty((4*n_simulations, N_T))
    V[:, 0]    = v0
    
    logS       = np.empty((4*n_simulations, N_T))
    logS[:, 0] = np.log(s0)

    sqrt1_rho2 = sqrt(1-rho**2)

    for n in prange(n_simulations):
        for i in range(0,  N_T-1):
            vmax             = max(V[4*n, i],0)
            sqrtvmaxdt       = sqrt(vmax*dt)
            logS[4*n, i+1]   = logS[4*n, i] + (r - 0.5 * vmax) * dt + sqrtvmaxdt * Z[0, n, i]
            V[4*n, i+1]      = V[4*n, i] + kappa*(vbar - vmax)*dt + gamma*sqrtvmaxdt*(rho*Z[0, n, i]+sqrt1_rho2*Z[1, n, i])

            vmax             = max(V[4*n+1, i],0)
            sqrtvmaxdt       = sqrt(vmax*dt)
            logS[4*n+1, i+1] = logS[4*n+1, i] + (r - 0.5 * vmax) * dt - sqrtvmaxdt * Z[0, n, i]
            V[4*n+1, i+1]    = V[4*n+1, i] + kappa*(vbar - vmax)*dt - gamma*sqrtvmaxdt*(rho*Z[0, n, i]+sqrt1_rho2*Z[1, n, i])

            vmax             = max(V[4*n+2, i],0)
            sqrtvmaxdt       = sqrt(vmax*dt)
            logS[4*n+2, i+1] = logS[4*n+2, i] + (r - 0.5 * vmax) * dt + sqrtvmaxdt * Z[0, n, i]
            V[4*n+2, i+1]    = V[4*n+2, i] + kappa*(vbar - vmax)*dt + gamma*sqrtvmaxdt*(rho*Z[0, n, i]-sqrt1_rho2*Z[1, n, i])

            vmax             = max(V[4*n+3, i],0)
            sqrtvmaxdt       = sqrt(vmax*dt)
            logS[4*n+3, i+1] = logS[4*n+3, i] + (r - 0.5 * vmax) * dt - sqrtvmaxdt * Z[0, n, i]
            V[4*n+3, i+1]    = V[4*n+3, i] + kappa*(vbar - vmax)*dt + gamma*sqrtvmaxdt*(-rho*Z[0, n, i] + sqrt1_rho2*Z[1, n, i])



    return [np.exp(logS), V]

@njit(parallel=True, cache=True, nogil=True)
def simulate_heston_andersen_qe(state:         MarketState,
                                heston_params: HestonParameters,
                                T:             float = 1.,
                                N_T:           int   = 100,
                                n_simulations: int   = 10_000,
                                Psi_c:         float = 1.5,
                                gamma_1:       float = 0.0     
                                ) -> np.ndarray: 
    """Simulation engine for the Heston model using the Quadratic-Exponential Andersen scheme.

    Args:
        state (MarketState):              Market state.
        heston_params (HestonParameters): Parameters of the Heston model.
        T (float, optional):              Contract termination time expressed as a non-integer amount of years. Defaults to 1..
        N_T (int, optional):              Number of steps in time. Defaults to 100.
        n_simulations (int, optional):    Number of simulations. Defaults to 10_000.
        Psi_c (float, optional):          Critical value of \psi, i.e. the moment of the scheme switching. Defaults to 1.5.
        gamma_1 (float, optional):        Integration parameter. Defaults to 0.5.

    Raises:
        Error: The critical value \psi_c must be in the interval [1,2]
        Error: The parameter \gamma_1 must be in the interval [0,1]

    Returns:
        A tuple containing the simulated stock price and the simulated stochastic variance.
        The number of paths is doubled to account for the antithetic variates.
    """    
    
    if Psi_c>2 or Psi_c<1:
        raise error('The critical value \psi_c must be in the interval [1,2]')
    if gamma_1 >1 or gamma_1<0:
        raise error('The parameter \gamma_1 must be in the interval [0,1]')
    if T <= 0:
        raise error("Contract termination time must be positive.")
        
    gamma_2 = 1.0 - gamma_1
    
    r, s0 = state.interest_rate, state.stock_price
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, heston_params.vbar, heston_params.gamma
    
    dt         = T/float(N_T)
    E          = exp(-kappa*dt)
    K_0        = -(rho*kappa*vbar/gamma)*dt
    K_1        = gamma_1 * dt * (rho*kappa/gamma - 0.5) - rho/gamma
    K_2        = gamma_2 * dt * (rho*kappa/gamma - 0.5) + rho/gamma
    K_3        = gamma_1 * dt * (1.0 - rho**2)
    K_4        = gamma_2 * dt * (1.0 - rho**2)
        
    V          = np.empty((4*n_simulations, N_T))
    V[:, 0]    = v0

    logS       = np.empty((4*n_simulations, N_T))
    logS[:, 0] = np.log(s0)

    Z          = np.random.standard_normal(size=(2, n_simulations, N_T))
    p1         = (1. - E)*(gamma**2)*E/kappa
    p2         = (vbar*gamma**2)/(2.0*kappa)*((1.-E)**2)
    p3         = vbar * (1.- E)
    rdtK0      = r*dt + K_0

    u = 0.

    for n in prange(n_simulations):
        for i in range(N_T - 1):
            m   = p3 + V[4*n, i]*E
            s_2 = V[4*n, i]*p1 + p2
            Psi = s_2/(m**2) 

            if Psi <= Psi_c:
                c           = 2. / Psi
                b           = c - 1. + sqrt(c*(c - 1.))
                a           = m/(1.+b)
                b           = sqrt(b)
                V[4*n, i+1] = a*((b+Z[1, n, i])**2)
            else:
                p           = (Psi - 1)/(Psi + 1)
                beta        = (1.0 - p)/m
                u           = Phi(Z[1, n, i])
                V[4*n,i+1]  = 0. if u < p else log((1-p)/(1-u))/beta

            logS[4*n,i+1] = logS[4*n,i] + rdtK0 + K_1*V[4*n,i] + K_2*V[4*n,i+1] + sqrt(K_3*V[4*n,i]+K_4*V[4*n,i+1]) * Z[0,n,i]

            m   = p3 + V[4*n+1, i]*E
            s_2 = V[4*n+1, i]*p1 + p2
            Psi = s_2/(m**2) 

            if Psi <= Psi_c:
                c             = 2. / Psi
                b             = c - 1. + sqrt(c*(c - 1.))
                a             = m/(1.+b)
                b             = sqrt(b)
                V[4*n+1, i+1] = a*((b-Z[1,n, i])**2)
            else:
                p             = (Psi - 1)/(Psi + 1)
                beta          = (1.0 - p)/m
                u             = Phi(- Z[1, n, i])
                V[4*n+1,i+1]  = 0. if u < p else log((1.-p)/(1.-u))/beta

            logS[4*n+1,i+1] = logS[4*n+1,i] + rdtK0 + K_1*V[4*n+1,i] + K_2*V[4*n+1,i+1] - sqrt(K_3*V[4*n+1,i]+K_4*V[4*n+1,i+1]) * Z[0,n,i]

            m   = p3 + V[4*n+2, i]*E
            s_2 = V[4*n+2, i]*p1 + p2
            Psi = s_2/(m**2)

            if Psi <= Psi_c:
                c             = 2. / Psi
                b             = c - 1. + sqrt(c*(c - 1.))
                a             = m/(1.+b)
                b             = sqrt(b)
                V[4*n+2, i+1] = a*((b-Z[1,n, i])**2)
            else:
                p             = (Psi - 1)/(Psi + 1)
                beta          = (1.0 - p)/m
                u             = Phi(- Z[1, n, i])
                V[4*n+2,i+1]  = 0. if u < p else log((1.-p)/(1.-u))/beta

            logS[4*n+2,i+1] = logS[4*n+2,i] + rdtK0 + K_1*V[4*n+2,i] + K_2*V[4*n+2,i+1] + sqrt(K_3*V[4*n+2,i]+K_4*V[4*n+2,i+1]) * Z[0,n,i]

            m   = p3 + V[4*n+3, i]*E
            s_2 = V[4*n+3, i]*p1 + p2
            Psi = s_2/(m**2)

            if Psi <= Psi_c:
                c             = 2. / Psi
                b             = c - 1. + sqrt(c*(c - 1.))
                a             = m/(1.+b)
                b             = sqrt(b)
                V[4*n+3, i+1] = a*((b+Z[1,n, i])**2)
            else:
                p             = (Psi - 1)/(Psi + 1)
                beta          = (1.0 - p)/m
                u             = Phi(Z[1, n, i])
                V[4*n+3,i+1]  = 0. if u < p else log((1.-p)/(1.-u))/beta

            logS[4*n+3,i+1] = logS[4*n+3,i] + rdtK0 + K_1*V[4*n+3,i] + K_2*V[4*n+3,i+1] - sqrt(K_3*V[4*n+3,i]+K_4*V[4*n+3,i+1]) * Z[0,n,i]
           
    return [np.exp(logS), V]


def calculate_r_for_andersen_tg(x_:      float,
                                maxiter: int = 2500, 
                                tol:     float = 1e-5
                                ):
    def foo(x: float):
        return x*norm.pdf(x) + norm.cdf(x)*(1+x**2) - (1+x_)*(norm.pdf(x) + x*norm.cdf(x))**2

    def foo_dif(x: float):
        return norm.pdf(x) - x**2 * norm.pdf(x) + norm.pdf(x)*(1+x**2) + 2*norm.cdf(x)*x - \
                2*(1+x_)*(norm.pdf(x) + x*norm.cdf(x))*(-norm.pdf(x)*x + norm.cdf(x) + x*norm.pdf(x) )

    def foo_dif2(x: float):
        return -x*norm.pdf(x) - 2*x* norm.pdf(x) + x**3 * norm.pdf(x) -x*norm.pdf(x)*(1+x**2) + \
                2*norm.cdf(x)*x + 2*norm.pdf(x)*x + 2*norm.cdf(x) + \
                2*(1+x_)*(-norm.pdf(x)*x + norm.cdf(x) + x*norm.pdf(x))**2 + \
                2*(1+x_)*(norm.pdf(x) + x*norm.cdf(x))*(x**2*norm.pdf(x) + norm.pdf(x) + norm.pdf(x) -x*norm.pdf(x) )

    return newton(foo,  x0 = 1/x_,fprime = foo_dif, fprime2 = foo_dif2, maxiter = maxiter , tol= tol )

@njit(parallel=True, cache=True, nogil=True)
def simulate_heston_andersen_tg(state:         MarketState,
                                heston_params: HestonParameters,
                                x_grid:        np.ndarray,
                                f_nu_grid:     np.ndarray,
                                f_sigma_grid:  np.ndarray,
                                T:             float = 1.,
                                N_T:           int   = 100,
                                n_simulations: int   = 10_000,
                                gamma_1:       float = 0.0
                                ) -> np.ndarray: 
    """ Simulation engine for the Heston model using the Truncated Gaussian Andersen scheme.

    Args:
        state (MarketState):              Market state.
        heston_params (HestonParameters): Parameters of the Heston model.
        x_grid (np.ndarray):              _description_
        f_nu_grid (np.ndarray):           _description_
        f_sigma_grid (np.ndarray):        _description_
        T (float, optional):              Contract termination time expressed as a non-integer amount of years. Defaults to 1..
        dt (float, optional):             Time step. Defaults to 1e-2.
        n_simulations (int, optional):    number of the simulations. Defaults to 10_000.
        gamma_1 (float, optional):        _description_. Defaults to 0.0.

    Raises:
        error: The parameter \gamma_1 must be in the interval [0,1].
        error: Contract termination time must be positive.

    Returns:
        A tuple containing the simulated stock price and the simulated stochastic variance.
        The number of paths is doubled to account for the antithetic variates.
    """    
    if gamma_1 >1 or gamma_1<0:
        raise error('The parameter \gamma_1 must be in the interval [0,1]')
    if T <= 0:
        raise error("Contract termination time must be positive.")
            
    r, s0 = state.interest_rate, state.stock_price
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, heston_params.vbar, heston_params.gamma
    
    gamma_2    = 1. - gamma_1
    dt         = T/float(N_T)
    E          = exp(-kappa*dt)
    K_0        = -(rho*kappa*vbar/gamma)*dt
    K_1        = gamma_1 * dt * (rho*kappa/gamma - 0.5) - rho/gamma
    K_2        = gamma_2 * dt * (rho*kappa/gamma - 0.5) + rho/gamma
    K_3        = gamma_1 * dt * (1.0 - rho**2)
    K_4        = gamma_2 * dt * (1.0 - rho**2)
        
    V          = np.empty((4*n_simulations, N_T))
    V[:, 0]    = v0
    logS       = np.empty((4*n_simulations, N_T))
    logS[:, 0] = np.log(s0)

    Z          = np.random.standard_normal(size=(2, n_simulations, N_T))
    #Z_V        = np.random.standard_normal(size=(n_simulations, N_T))    #do we need this?  
    p1         = (1. - E)*(gamma**2)*E/kappa
    p2         = (vbar*gamma**2)/(2.0*kappa)*((1.-E)**2)
    p3         = vbar * (1.- E)
    rdtK0      = r*dt + K_0
    dx         = x_grid[1] - x_grid[0]
    
    for n in prange(n_simulations):
        for i in range(N_T - 1):
            m               = p3 + V[4*n, i]*E
            s_2             = V[4*n, i]*p1 + p2
            Psi             = s_2/(m**2) 


            if Psi > x_grid[-1]:
                inx         = x_grid.shape[0] -1
            else:
                inx         = int(Psi/dx)
        
            nu              = m*f_nu_grid[inx]
            sigma           = sqrt(s_2)*f_sigma_grid[inx]

            V[4*n, i+1]     = max(nu + sigma*Z[1, n, i], 0)
            logS[4*n,i+1]   = logS[4*n,i] + rdtK0 + K_1*V[4*n,i] + K_2*V[4*n,i+1] + sqrt(K_3*V[4*n,i]+K_4*V[4*n,i+1]) * Z[0, n,i]

            m               = p3 + V[4*n+1, i]*E
            s_2             = V[4*n+1, i]*p1 + p2
            Psi             = s_2/(m**2) 

            if Psi > x_grid[-1]:
                inx         = x_grid.shape[0] -1
            else:
                inx         = int(Psi/dx)
        
            nu              = m * f_nu_grid[inx]
            sigma           = np.sqrt(s_2)*f_sigma_grid[inx]

            V[4*n+1,i+1]    = max(nu - sigma*Z[1, n, i], 0)
            logS[4*n+1,i+1] = logS[4*n+1,i] + rdtK0 + K_1*V[4*n+1,i] + K_2*V[4*n+1,i+1] - sqrt(K_3*V[4*n+1,i]+K_4*V[4*n+1,i+1]) * Z[0,n,i]
            
            if Psi > x_grid[-1]:
                inx         = x_grid.shape[0] -1
            else:
                inx             = int(Psi/dx)
        
            nu              = m * f_nu_grid[inx]
            sigma           = np.sqrt(s_2)*f_sigma_grid[inx]

            V[4*n+2,i+1]    = max(nu - sigma*Z[1, n, i], 0)
            logS[4*n+2,i+1] = logS[4*n+2,i] + rdtK0 + K_1*V[4*n+2,i] + K_2*V[4*n+2,i+1] + sqrt(K_3*V[4*n+2,i]+K_4*V[4*n+2,i+1]) * Z[0,n,i]
            
            if Psi > x_grid[-1]:
                inx         = x_grid.shape[0] -1
            else:
                inx             = int(Psi/dx)
        
            nu              = m * f_nu_grid[inx]
            sigma           = np.sqrt(s_2)*f_sigma_grid[inx]

            V[4*n+3,i+1]    = max(nu + sigma*Z[1, n, i], 0)
            logS[4*n+3,i+1] = logS[4*n+3,i] + rdtK0 + K_1*V[4*n+3,i] + K_2*V[4*n+3,i+1] - sqrt(K_3*V[4*n+3,i]+K_4*V[4*n+3,i+1]) * Z[0,n,i]
            

    return [np.exp(logS), V]