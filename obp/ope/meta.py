# Copyright (c) ZOZO Technologies, Inc. All rights reserved.
# Licensed under the Apache 2.0 License.

"""Off-Policy Evaluation Class to Streamline OPE."""
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .estimators import BaseOffPolicyEstimator
from .regression_model import RegressionModel
from ..dataset import BanditFeedback
from ..utils import check_is_fitted


@dataclass
class OffPolicyEvaluation:
    """Class to do off-policy evaluation by multiple off-policy estimators.

    Note
    ------
    When you use model dependent estimators such as Direct Method and Doubly Robust,
    you must give action context and regression model when defining this class.

    Parameters
    -----------
    bandit_feedback: BanditFeedback
        Logged bandit feedback data to be used in offline bandit simulation.

    ope_estimators: List[BaseOffPolicyEstimator]
        List of OPE estimators used to evaluate the policy value of counterfactual (or evaluation) policy.
        Estimators must follow the interface of `obp.ope.BaseOffPolicyEstimator`.

    action_context: array-like, shape (n_actions, dim_action_context), default: None
        Context vectors used as input to predict the mean reward function.

    regression_model: RegressionModel, default: None
        Regression model that predicts the mean reward function :math:`E[Y | X, A]`.

    Examples
    ----------
    # a case for implementing OPE of the BernoulliTS policy
    # using log data generated by the Random policy
    >>> from obp.dataset import OpenBanditDataset
    >>> from obp.policy import BernoulliTS
    >>> from obp.simulator import run_bandit_simulation
    >>> from obp.ope import OffPolicyEvaluation, ReplayMethod

    # (1) Data loading and preprocessing
    >>> dataset = OpenBanditDataset(behavior_policy='random', campaign='all')
    >>> train, test = dataset.split_data(test_size=0.3, random_state=42)
    >>> train.keys()
    dict_keys(['n_rounds', 'n_actions', 'action', 'position', 'reward', 'pscore', 'context', 'action_context'])

    # (2) Offline Bandit Simulation
    >>> counterfactual_policy = BernoulliTS(n_actions=dataset.n_actions, len_list=dataset.len_list)
    >>> selected_actions = run_bandit_simulation(train=train, policy=counterfactual_policy)
    >>> selected_actions
    array([[24, 14, 30],
          [30, 27, 24],
          [14, 12, 32],
           ...,
          [17, 13, 30],
          [20, 34,  6],
          [30,  3, 20]])

    # (3) Off-Policy Evaluation
    >>> ope = OffPolicyEvaluation(train=train, ope_estimators=[ReplayMethod()])
    >>> estimated_policy_value = ope.estimate_policy_values(selected_actions=selected_actions)
    >>> estimated_policy_value
    {'rm': 0.003717..}

    # estimated performance of BernoulliTS relative to the ground-truth performance of Random
    >>> relative_policy_value_of_bernoulli_ts = estimated_policy_value['rm'] / test['reward'].mean()
    >>> relative_policy_value_of_bernoulli_ts
    1.21428...

    """
    bandit_feedback: BanditFeedback
    ope_estimators: List[BaseOffPolicyEstimator]
    action_context: Optional[np.ndarray] = None
    regression_model: Optional[RegressionModel] = None

    def __post_init__(self) -> None:
        """Initialize class."""
        if (self.regression_model is not None):
            if check_is_fitted(self.regression_model):
                logging.info("a fitted regression model is given.")
            else:
                logging.info("the given regression model is not fitted, and thus train it here...")
                self.regression_model.fit(
                    bandit_feedback=self.bandit_feedback,
                    action_context=self.action_context
                )
        else:
            logging.warning(
                "regression model is not given; model dependent estimators such as DM or DR cannot be used."
            )

        self.ope_estimators_ = dict()
        for estimator in self.ope_estimators:
            self.ope_estimators_[estimator.estimator_name] = estimator

    def _create_estimator_inputs(self, selected_actions: np.ndarray) -> Dict[str, np.ndarray]:
        """Create input dictionary to estimate policy value by subclasses of `BaseOffPolicyEstimator`"""
        estimator_inputs = {input_: self.bandit_feedback[input_] for input_ in ['reward', 'pscore']}
        estimator_inputs['action_match'] =\
            self.bandit_feedback['action'] == selected_actions[
                np.arange(self.bandit_feedback['n_rounds']), self.bandit_feedback['position']]
        if self.regression_model is not None:
            estimator_inputs['estimated_rewards_by_reg_model'] = self.regression_model.predict(
                bandit_feedback=self.bandit_feedback,
                action_context=self.action_context,
                selected_actions=selected_actions
            )
        return estimator_inputs

    def estimate_policy_values(self, selected_actions: np.ndarray) -> Dict[str, float]:
        """Estimate policy value of a counterfactual policy.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        Returns
        ----------
        policy_value_dict: Dict[str, float]
            Dictionary containing estimated policy values by off-policy estimators.

        """
        policy_value_dict = dict()
        estimator_inputs = self._create_estimator_inputs(selected_actions=selected_actions)
        for estimator_name, estimator in self.ope_estimators_.items():
            policy_value_dict[estimator_name] = estimator.estimate_policy_value(**estimator_inputs)

        return policy_value_dict

    def estimate_intervals(self,
                           selected_actions: np.ndarray,
                           alpha: float = 0.05,
                           n_bootstrap_samples: int = 100,
                           random_state: Optional[int] = None) -> Dict[str, Dict[str, float]]:
        """Estimate confidence interval of policy value by nonparametric bootstrap procedure.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        alpha: float, default: 0.05
            P-value.

        n_bootstrap_samples: int, default: 100
            Number of resampling performed in the bootstrap procedure.

        random_state: int, default: None
            Controls the random seed in bootstrap sampling.

        Returns
        ----------
        policy_value_interval_dict: Dict[str, Dict[str, float]]
            Dictionary containing confidence intervals of policy value estimated by nonparametric booststrap procedure.

        """
        policy_value_interval_dict = dict()
        estimator_inputs = self._create_estimator_inputs(selected_actions=selected_actions)
        for estimator_name, estimator in self.ope_estimators_.items():
            policy_value_interval_dict[estimator_name] = estimator.estimate_interval(
                **estimator_inputs, alpha=alpha, n_bootstrap_samples=n_bootstrap_samples, random_state=random_state)

        return policy_value_interval_dict

    def summarize_off_policy_estimates(self,
                                       selected_actions: np.ndarray,
                                       alpha: float = 0.05,
                                       n_bootstrap_samples: int = 100,
                                       random_state: Optional[int] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Summarize estimated_policy_values and their confidence intervals in off-policy evaluation by given estimators.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        alpha: float, default: 0.05
            P-value.

        n_bootstrap_samples: int, default: 100
            Number of resampling performed in the bootstrap procedure.

        random_state: int, default: None
            Controls the random seed in bootstrap sampling.

        Returns
        ----------
        (policy_value_df, policy_value_interval_df): Tuple[pd.DataFrame, pd.DataFrame]
            Estimated policy values and their confidence intervals made by given off-policy estimators.

        """
        policy_value_df = pd.DataFrame(
            self.estimate_policy_values(selected_actions=selected_actions),
            index=['estimated_policy_value']
        )
        policy_value_interval_df = pd.DataFrame(
            self.estimate_intervals(
                selected_actions=selected_actions,
                alpha=alpha,
                n_bootstrap_samples=n_bootstrap_samples,
                random_state=random_state)
        )

        return policy_value_df.T, policy_value_interval_df.T

    def visualize_off_policy_estimates(self,
                                       selected_actions: np.ndarray,
                                       alpha: float = 0.05,
                                       n_bootstrap_samples: int = 100,
                                       random_state: Optional[int] = None,
                                       fig_dir: Optional[Path] = None,
                                       fig_name: Optional[str] = None) -> None:
        """Visualize estimated policy values by given off-policy evaluation.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        alpha: float, default: 0.05
            P-value.

        n_bootstrap_samples: int, default: 100
            Number of resampling performed in the bootstrap procedure.

        random_state: int, default: None
            Controls the random seed in bootstrap sampling.

        fig_dir: Path, default: None
            Dierctory to store the bar figure.
            If 'None' is given, the figure will not be saved.

        fig_dir: Path, default: None
            Name of the bar figure.
            If 'None' is given, 'estimated_policy_value.png' will be used.

        """
        estimated_round_rewards_dict = dict()
        estimator_inputs = self._create_estimator_inputs(selected_actions=selected_actions)
        for estimator_name, estimator in self.ope_estimators_.items():
            estimated_round_rewards_dict[estimator_name] = estimator._estimate_round_rewards(**estimator_inputs)
        estimated_round_rewards_df = pd.DataFrame(estimated_round_rewards_dict)
        estimated_round_rewards_df.rename(
            columns={key: key.upper() for key in estimated_round_rewards_dict.keys()},
            inplace=True
        )

        fig, ax = plt.subplots(figsize=(8, 6))
        sns.barplot(data=estimated_round_rewards_df, ax=ax,
                    ci=100 * (1 - alpha), n_boot=n_bootstrap_samples)
        plt.xlabel("OPE Estimators", fontsize=25)
        plt.ylabel(f"Estimated Policy Value (± {np.int(100*(1 - alpha))}% CI)", fontsize=20)
        plt.yticks(fontsize=15)
        plt.xticks(fontsize=20)

        if fig_dir:
            fig_name = fig_name if fig_name is not None else 'estimated_policy_value.png'
            fig.savefig(str(fig_dir / fig_name))

    def evaluate_performance_of_estimators(self,
                                           selected_actions: np.ndarray,
                                           ground_truth_policy_value: float) -> Dict[str, float]:
        """Evaluate results of off-policy estimators by relative estimation error.

        Evaluate the performacnce of off-policy estimators by the following relative estimation error.

        .. math ::

            \\text{Relative-Estimation Error of } \\hat{V}^{\\pi} = \\left|  \\frac{ \\hat{V}^{\\pi} - V^{\\pi}}{V^{\\pi}} \\right|.

        where :math:`V^{\\pi}` is a ground-truth policy value of :math:`\\pi` in a test set.
        :math:`\\hat{V}^{\\pi}` is an estimated policy value by each off-policy estiamtor with logged bandit feedback data.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        ground_truth policy value: int
            Ground_truth policy value of a counterfactual policy, i.e., :math:`V(\\pi)`.
            With Open Bandit Dataset, in general, we use an on-policy estimate of the policy value as ground-truth.

        Returns
        ----------
        relative_estimation_error_dict: Dict[str, float]
            Dictionary containing relative estimation error of off-policy estimators.

        """
        relative_estimation_error_dict = dict()
        estimator_inputs = self._create_estimator_inputs(selected_actions=selected_actions)
        for estimator_name, estimator in self.ope_estimators_.items():
            estimated_policy_value = estimator.estimate_policy_value(**estimator_inputs)
            relative_estimation_error_dict[estimator_name] =\
                np.abs((estimated_policy_value - ground_truth_policy_value) / ground_truth_policy_value)

        return relative_estimation_error_dict

    def summarize_estimators_comparison(self, selected_actions: np.ndarray) -> pd.DataFrame:
        """Summarize performance comparison of given off-policy estimators.

        Parameters
        ----------
        selected_actions: array-like, shape (n_rounds, len_list)
            Lists of actions selected by counterfactual (or evaluation) policy at each round in offline bandit simulation.

        Returns
        ----------
        relative_estimation_error_df: pd.DataFrame
            Estimated policy values and their confidence intervals made by given off-policy estimators.

        """
        relative_estimation_error_df = pd.DataFrame(
            self.evaluate_performance_of_estimators(selected_actions=selected_actions),
            index=['relative_estimation_error']
        )

        return relative_estimation_error_df.T
