import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
from scipy.optimize import differential_evolution
from scipy.stats import norm, poisson
from joblib import Parallel, delayed


# --------------------------------------------------------------------------------
# Helper Functions 
# --------------------------------------------------------------------------------

def compute_log_returns(prices):
    """Compute log returns from a price series."""
    return np.log(prices / prices.shift(1)).dropna()

def identify_jumps(log_returns, jump_threshold_sigma_multiples=2):
    """Identify jumps in log returns based on a multiple of the standard deviation."""
    sigma = log_returns.std()
    jump_threshold = jump_threshold_sigma_multiples * sigma
    return np.abs(log_returns) > jump_threshold

def filter_extremes(data, lower_quantile=0.01, upper_quantile=0.99):
    lower_bound = np.quantile(data, lower_quantile)
    upper_bound = np.quantile(data, upper_quantile)
    return data[(data >= lower_bound) & (data <= upper_bound)]

def plot_data(prices, returns, jumps=None):
    """Plot prices, log returns, and mark identified jumps."""
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))

    # Plot price series
    axes[0].plot(prices)
    axes[0].set_title('Stock Prices')
    axes[0].set_ylabel('Price ($)')
    axes[0].grid(True)

    # Plot log returns
    axes[1].plot(returns)
    axes[1].set_title('Daily Log Returns')
    axes[1].set_ylabel('Log Return')
    axes[1].grid(True)

    # Plot log returns with jumps highlighted
    axes[2].plot(returns, label='Log Returns')
    if jumps is not None:
        axes[2].scatter(returns.index[jumps], returns[jumps], color='red', label='Jumps')
    axes[2].set_title('Daily Log Returns with Jumps')
    axes[2].set_ylabel('Log Return')
    axes[2].grid(True)
    axes[2].legend()

    plt.tight_layout()
    plt.show()

# --------------------------------------------------------------------------------
# Merton Jump Diffusion with Jump Drift Correction
# --------------------------------------------------------------------------------

def merton_jump_diffusion_paths(S0, r, sigma, lambda_, mu_J, sigma_J, num_steps, num_simulations, dt=1):
    """
    Fully vectorized simulation of multiple stock price paths using the Merton jump-diffusion model.

    Parameters:
      S0       : Initial stock price.
      r        : Drift term (risk-free rate).
      sigma    : Diffusion volatility.
      lambda_  : Jump intensity (expected number of jumps per unit time).
      mu_J     : Mean of jump size in log-space.
      sigma_J  : Standard deviation of jump size in log-space.
      num_steps: Number of time steps.
      num_simulations: Number of simulation paths.
      dt       : Size of each time step.
      
    Returns:
      A NumPy array of simulated stock prices with shape (num_simulations, num_steps + 1).
    """
    # Jump drift correction term
    m = np.exp(mu_J + 0.5 * sigma_J**2) - 1  

    # Initialize price paths
    S = np.zeros((num_simulations, num_steps + 1))
    S[:, 0] = S0  # Set initial prices

    # Precompute random components
    dW = np.random.normal(0, np.sqrt(dt), size=(num_simulations, num_steps))  # Brownian motion
    N_t = np.random.poisson(lambda_ * dt, size=(num_simulations, num_steps))  # Number of jumps
    jumps = np.random.normal(mu_J, sigma_J, size=(num_simulations, num_steps)) * N_t  # Jump sizes

    # Compute log returns
    diffusion = (r - lambda_ * m - 0.5 * sigma**2) * dt + sigma * dW
    log_returns = diffusion + jumps

    # Compute price paths
    S[:, 1:] = S0 * np.exp(np.cumsum(log_returns, axis=1))
    S = np.maximum(S, 1e-8)  # Ensure no negative prices

    return S

def calculate_mse(params, historical_returns, S0, r, num_steps, num_simulations):
    """
    Compute the mean squared error (MSE) between historical log returns and simulated log returns.

    Parameters:
      params                : Model parameters [sigma, lambda, mu_J, sigma_J].
      historical_returns    : Historical log returns (Pandas Series).
      S0                    : Initial stock price.
      r                     : Risk-free rate.
      num_steps             : Number of time steps.
      num_simulations       : Number of simulated paths.
    
    Returns:
      Mean squared error (MSE) between simulated and historical log returns.
    """
    sigma, lambda_, mu_J, sigma_J = params

    # Simulate multiple stock price paths (vectorized version)
    simulated_paths = merton_jump_diffusion_paths(S0, r, sigma, lambda_, mu_J, sigma_J, num_steps, num_simulations)

    # Convert to log returns
    simulated_log_returns = np.log(simulated_paths[:, 1:] / simulated_paths[:, :-1])

    # Compute the average log returns across all simulated paths
    mean_simulated_returns = np.mean(simulated_log_returns, axis=0)

    # Ensure equal lengths for comparison
    min_len = min(len(mean_simulated_returns), len(historical_returns))
    mean_simulated_returns = mean_simulated_returns[:min_len]
    hist_returns = historical_returns.values[:min_len]

    # Check for invalid values
    if np.any(np.isnan(mean_simulated_returns)) or np.any(np.isinf(mean_simulated_returns)):
        return np.inf  # Return high error to reject bad parameters

    # Compute MSE
    mse = np.mean((mean_simulated_returns - hist_returns) ** 2)

    return mse


# --------------------------------------------------------------------------------
# Parameter Calibration with Differential Evolution
# --------------------------------------------------------------------------------

def calibrate_merton_parameters(historical_returns, S0, r, num_steps, num_simulations_calibration):
    """
    Calibrate Merton model parameters by minimizing the MSE between simulated and historical returns.
    
    Returns:
      The result of the differential evolution optimization.
    """
    bounds = [
        (0.01, 2.0),   # sigma
        (0.01, 1.0),   # lambda
        (-0.5, 0.5),     # mu_J
        (0.01, 1.0)    # sigma_J
    ]
    
    result = differential_evolution(
        calculate_mse,
        bounds=bounds,
        args=(historical_returns, S0, r, num_steps, num_simulations_calibration),
        maxiter=1000,
        tol=1e-2,
        disp=True,
        popsize=50,
        workers=-1
    )
    
    return result, result.fun

# Function to run multiple calibration runs
def run_multiple_calibrations(historical_returns, S0, r, num_steps, num_simulations_calibration, num_runs):
    results = []
    calibrated_parameters = []
    mses = []
    for i in range(num_runs):
        print(f"Running calibration {i+1}/{num_runs}...")
        result, mse = calibrate_merton_parameters(historical_returns, S0, r, num_steps, num_simulations_calibration)
        if result.success:
            results.append(result)
            calibrated_parameters.append(result.x)
            mses.append(mse)
    if calibrated_parameters:
        avg_params = np.mean(calibrated_parameters, axis=0)
        avg_mse = np.mean(mses)
    else:
        avg_params = None
        avg_mse = None
    return results, avg_params, avg_mse

# --------------------------------------------------------------------------------
# Option Pricing
# --------------------------------------------------------------------------------

# Compute B.S. call and put
def black_scholes_option_pricing(ST, K, T, r_annual, sigma_annual, option_type):
    """Calculates the Black-Scholes call and put option prices."""
    d1 = (np.log(ST / K) + (r_annual + 0.5 * sigma_annual ** 2) * T) / (sigma_annual * np.sqrt(T))
    d2 = d1 - sigma_annual * np.sqrt(T)
    if option_type == 'call':
        bs_option_price = ST * norm.cdf(d1) - K * np.exp(-r_annual * T) * norm.cdf(d2)
    elif option_type == 'put':
        bs_option_price = K * np.exp(-r_annual * T) * norm.cdf(-d2) - ST * norm.cdf(-d1)
    else:
        raise ValueError("option_type must be 'call' or 'put'")
    
    return bs_option_price

# Compute B.S. with Jump Diffusion call and put
def compute_jump_parameters(r_annual, sigma_annual, lambda_annual, mu_J_annual, sigma_J_annual, T):
    """
    Compute the adjusted risk-free rate (r_n) and volatility (sigma_n) under jumps.

    Parameters:
      r_annual : Annualized risk-free rate.
      sigma_annual : Annualized volatility.
      lambda_ : Jump intensity (expected jumps per year).
      mu_J : Mean of jump size in log-space.
      sigma_J : Std deviation of jump size in log-space.
      T : Time to maturity in years.

    """
    
    # Simulate Poisson-distributed number of jumps
    n_jumps = np.random.poisson(lambda_annual * T)

    # Standard normal variable
    Z = np.random.normal(0, 1)

    # Expected jump adjustment
    m_annual = np.exp(mu_J_annual + 0.5 * sigma_J_annual**2) - 1

    # Adjusted risk-free rate under jumps
    r_n = r_annual - lambda_annual * m_annual + (n_jumps * np.log(1 + m_annual))/T

    # Adjusted volatility under jumps
    sigma_n = np.sqrt(sigma_annual**2 + n_jumps * sigma_J_annual**2 / T)
    
    # Adjusted lambda under jumps
    lambda_n = lambda_annual * (1 + m_annual)

    return n_jumps, Z, m_annual, r_n, sigma_n, lambda_n

def stock_price_with_jumps(S0, r_n, sigma_n, T, Z):
    """
    Compute S(T) under the jump-diffusion model given N(T) = n.

    Parameters:
      S0 : Initial stock price.
      r_n : Adjusted risk-free rate under jumps.
      sigma_n : Adjusted volatility under jumps.
      T : Time to maturity.
      Z : Standard normal random variable.

    Returns:
      The simulated stock price S(T).
    """
    return S0 * np.exp(r_n * T - 0.5 * sigma_n**2 * T + sigma_n * np.sqrt(T) * Z)


def black_scholes_with_jumps(ST, K, T, r_annual, sigma_annual, lambda_annual, mu_J_annual, sigma_J_annual, option_type, num_simulations):
    """
    Compute the option price under the Black-Scholes model with jumps.

    Parameters:
      S0 : Initial stock price.
      K : Strike price.
      T : Time to maturity.
      r_annual : Annualized risk-free rate.
      sigma_annual : Annualized volatility.
      lambda_ : Jump intensity.
      mu_J : Mean of jump size in log-space.
      sigma_J : Standard deviation of jump size in log-space.

    Returns:
      Option price under the jump-diffusion model.
    """
    bs_jumps_option_price = 0  # Initialize
    for _ in range(num_simulations): 
        for n_jumps in range(10):
            # Compute jump parameters for each scenario
            n_jumps, Z, m_annual, r_n, sigma_n, lambda_n = compute_jump_parameters(r_annual, sigma_annual, lambda_annual, mu_J_annual, sigma_J_annual, T)
            
            # Poisson probability weight
            poisson_weight = np.exp(poisson.logpmf(n_jumps, lambda_n * T))

            # Prevent extreme exponentiation
            safe_r_n_T = max(min(r_n * T, 700), -700)   # Clamp r_n * T
            discount_factor = np.exp(-safe_r_n_T)       # Apply exponentiation safely
            
            # Black-Scholes components
            d1 = (np.log(ST / K) + (r_n + 0.5 * sigma_n**2) * T) / (sigma_n * np.sqrt(T))
            d2 = d1 - sigma_n * np.sqrt(T)

            d1 = np.clip(d1, -10, 10)       # Avoid extreme values
            d2 = np.clip(d2, -10, 10)

            
            # Standard Black-Scholes formula with jump influence                                                           
            if option_type == 'call':
                bsc = ST * norm.cdf(d1) - K * discount_factor * norm.cdf(d2)
            elif option_type == 'put':
                bsc = K * discount_factor * norm.cdf(-d2) - ST * norm.cdf(-d1)
            else:
                raise ValueError("option_type must be 'call' or 'put'")

        
            bs_jumps_option_price += poisson_weight * bsc
    return bs_jumps_option_price/num_simulations


# Compute M.C. call and put
def monte_carlo_option_pricing(S0, r, r_annual, sigma, lambda_, mu_J, sigma_J, T, K, option_type, num_simulations, num_steps):
    """
    Monte Carlo simulation for option pricing under the Merton Jump-Diffusion Model.
    
    Parameters:
      S0            : Initial stock price.
      r             : Risk-free rate.
      sigma         : Diffusion volatility.
      lambda_       : Jump intensity.
      mu_J          : Mean jump size (log-space).
      sigma_J       : Jump volatility.
      T             : Time to maturity (years).
      K             : Strike price.
      option_type   : 'call' or 'put'.
      num_simulations: Number of simulation paths.
      dummy_num_steps: This argument will be overwritten.
      
    Returns:
      Monte Carlo option price.
    """
    # Compute number of steps based on T (assuming 252 trading days per year)
    num_steps = int(252 * T)
    
    # Use the fully vectorized simulation function once:
    simulated_paths = merton_jump_diffusion_paths(S0=ST, r=r, sigma=sigma_calibrated, lambda_=lambda_calibrated,
                                                mu_J=mu_J_calibrated, sigma_J=sigma_J_calibrated, num_steps=num_steps,
                                                num_simulations=num_simulations
    )
    
    
    # Determine the payoff based on the option type
    if option_type == 'call':
        payoffs = np.maximum(simulated_paths[:, -1] - K, 0)
    elif option_type == 'put':
        payoffs = np.maximum(K - simulated_paths[:, -1], 0)
    else:
        raise ValueError("option_type must be 'call' or 'put'")
    
    # Discount average payoff to present value
    mc_option_price = np.exp(-r_annual * T) * np.mean(payoffs)
    return mc_option_price


# --------------------------------------------------------------------------------
# Main Execution
# --------------------------------------------------------------------------------

if __name__ == "__main__":
    file_path = Path(r"C:\Users\inve-\OneDrive\Documenti\ko.xlsx")  # Update the file path as needed

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
        prices = df['Adj Close']
        log_returns = compute_log_returns(prices)
        
        if log_returns is not None:
            # Identify historical jumps for analysis.
            jumps = identify_jumps(log_returns)
            print(f"\nTotal number of historical jumps (threshold-based): {jumps.sum()}")
            plot_data(prices, log_returns, jumps)

            S0 = prices.iloc[0]
            ST = prices.iloc[-1]
            r = 0.0002
            num_steps = len(prices) - 1
            num_simulations = 100000
            num_simulations_calibration = 1
            num_runs = 1  # Number of calibration runs
            
            
            # Initial guesses
            sigma_estimate = log_returns.std()
            lambda_estimate = jumps.sum() / (len(prices) - 1)
            mu_J_estimate = log_returns[jumps].mean()
            sigma_J_estimate = log_returns[jumps].std()
            initial_guess = [sigma_estimate, lambda_estimate, mu_J_estimate, sigma_J_estimate]
            print("Initial guesses:")
            print(f"  sigma   : {initial_guess[0]:.6f}")
            print(f"  lambda  : {initial_guess[1]:.6f}")
            print(f"  mu_J    : {initial_guess[2]:.6f}")
            print(f"  sigma_J : {initial_guess[3]:.6f}")

            # Calibrate the Merton model parameters

            # Run multiple calibration runs and compute the average
            calibration_results, avg_calibrated_params, avg_mse = run_multiple_calibrations(
                log_returns, S0, r, num_steps, num_simulations_calibration, num_runs
            )

            print(f"\nMSE: {avg_mse:.6f}")
                    
            # Print the average of the calibrated parameters (if available)
            if avg_calibrated_params is not None:
                sigma_calibrated, lambda_calibrated, mu_J_calibrated, sigma_J_calibrated = avg_calibrated_params
                print("\nAverage Calibrated Parameters:")
                print(f"  sigma   : {avg_calibrated_params[0]:.6f}")
                print(f"  lambda  : {avg_calibrated_params[1]:.6f}")
                print(f"  mu_J    : {avg_calibrated_params[2]:.6f}")
                print(f"  sigma_J : {avg_calibrated_params[3]:.6f}")
            
            # Simulate multiple paths with the calibrated parameters.
                simulated_paths = merton_jump_diffusion_paths(
                    S0=S0,
                    r=r,  # Using r as the baseline drift
                    sigma=sigma_calibrated,
                    lambda_=lambda_calibrated,
                    mu_J=mu_J_calibrated,
                    sigma_J=sigma_J_calibrated,
                    num_steps=num_steps,
                    num_simulations=num_simulations
                )

                # Convert daily parameters to annualized
                r_annual = r * 252  
                sigma_annual = sigma_calibrated * np.sqrt(252)
                lambda_annual = lambda_calibrated * 252
                mu_J_annual = mu_J_calibrated * 252
                sigma_J_annual = sigma_J_calibrated * np.sqrt(252)
                
                # Option pricing
                print(f"  S   : {ST:.6f}")
                T = float(input("Enter time to maturity (T) in years: "))
                K = float(input("Enter strike price (K): "))
                option_type = input("Enter option type ('call' or 'put'): ").strip().lower()
                 
                bs_option_price = black_scholes_option_pricing(ST, K, T, r_annual, sigma_annual, option_type)
                print(f"The estimated {option_type} Black-Scholes option price is: {bs_option_price:.4f}")
                
                bs_jumps_option_price = black_scholes_with_jumps(S0, K, T, r_annual, sigma_annual, lambda_annual, mu_J_annual, sigma_J_annual, option_type , num_simulations=1000)
                print(f"The estimated {option_type} Black-Scholes with jumps option price is: {bs_jumps_option_price:.4f}")
                
                mc_option_price = monte_carlo_option_pricing(S0, r, r_annual, sigma_calibrated, lambda_calibrated, mu_J_calibrated, sigma_J_calibrated, T, K, option_type, num_simulations, None)
                print(f"The estimated {option_type} Monte Carlo option price is: {mc_option_price:.4f}")
                
                # Adjust lengths to match historical data.
                min_length = min(len(prices), simulated_paths.shape[1])
                historical_prices = prices.iloc[:min_length]
                simulated_paths = simulated_paths[:, :min_length]

                # Plot simulated paths along with historical prices.
                plt.figure(figsize=(10, 6))
                for path in simulated_paths:
                    plt.plot(path, linestyle="dashed", alpha=0.7)
                plt.plot(historical_prices.values, color="black", linewidth=2, label="Historical")
                plt.title('Merton Jump Diffusion Simulated Paths')
                plt.xlabel('Time Steps')
                plt.ylabel('Stock Price')
                plt.grid(True)
                plt.show()

                # Final simulated values.
                average_final_value = np.mean(simulated_paths[:, -1])
                print("Average simulated value at final time step:", average_final_value)

                # Histogram of final simulated prices.
                plt.figure(figsize=(10, 6))
                plt.hist(simulated_paths[:, -1], bins=500, edgecolor='black')
                plt.title('Distribution of Final Simulated Values')
                plt.xlabel('Final Value')
                plt.ylabel('Frequency')
                plt.grid(True)
                plt.show()

                # Histogram of simulated log returns.
                all_simulated_log_returns = []
                for path in simulated_paths:
                    log_ret = np.log(path[1:] / path[:-1])
                    all_simulated_log_returns.extend(log_ret)
                all_simulated_log_returns = np.array(all_simulated_log_returns)
                plt.figure(figsize=(10, 6))
                plt.hist(all_simulated_log_returns, bins=200, edgecolor='black', alpha=0.7)
                plt.title('Histogram of Simulated Log Returns')
                plt.xlabel('Log Return')
                plt.ylabel('Frequency')
                plt.grid(True)
                plt.show()


            else:
                print("\nNo successful calibration runs, cannot compute average parameters.")


    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
    except Exception as e:
        print(f"An error occurred: {e}")
