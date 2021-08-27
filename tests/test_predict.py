import gym
import numpy as np
import pytest
import torch as th
import torch.nn as nn

from stable_baselines3 import A2C, DQN, PPO, SAC, TD3
from stable_baselines3.common.preprocessing import get_flattened_obs_dim
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.utils import get_device
from stable_baselines3.common.vec_env import DummyVecEnv

MODEL_LIST = [
    PPO,
    A2C,
    TD3,
    SAC,
    DQN,
]


@pytest.mark.parametrize("model_class", MODEL_LIST)
def test_auto_wrap(model_class):
    # test auto wrapping of env into a VecEnv

    # Use different environment for DQN
    if model_class is DQN:
        env_name = "CartPole-v0"
    else:
        env_name = "Pendulum-v0"
    env = gym.make(env_name)
    eval_env = gym.make(env_name)
    model = model_class("MlpPolicy", env)
    model.learn(100, eval_env=eval_env)


@pytest.mark.parametrize("model_class", MODEL_LIST)
@pytest.mark.parametrize("env_id", ["Pendulum-v0", "CartPole-v1"])
@pytest.mark.parametrize("device", ["cpu", "cuda", "auto"])
def test_predict(model_class, env_id, device):
    if device == "cuda" and not th.cuda.is_available():
        pytest.skip("CUDA not available")

    if env_id == "CartPole-v1":
        if model_class in [SAC, TD3]:
            return
    elif model_class in [DQN]:
        return

    # Test detection of different shapes by the predict method
    model = model_class("MlpPolicy", env_id, device=device)
    # Check that the policy is on the right device
    assert get_device(device).type == model.policy.device.type

    env = gym.make(env_id)
    vec_env = DummyVecEnv([lambda: gym.make(env_id), lambda: gym.make(env_id)])

    obs = env.reset()
    action, _ = model.predict(obs)
    assert action.shape == env.action_space.shape
    assert env.action_space.contains(action)

    vec_env_obs = vec_env.reset()
    action, _ = model.predict(vec_env_obs)
    assert action.shape[0] == vec_env_obs.shape[0]

    # Special case for DQN to check the epsilon greedy exploration
    if model_class == DQN:
        model.exploration_rate = 1.0
        action, _ = model.predict(obs, deterministic=False)
        assert action.shape == env.action_space.shape
        assert env.action_space.contains(action)

        action, _ = model.predict(vec_env_obs, deterministic=False)
        assert action.shape[0] == vec_env_obs.shape[0]


class FlattenBatchNormDropoutExtractor(BaseFeaturesExtractor):
    """
    Feature extract that flatten the input and applies batch normalization and dropout.
    Used as a placeholder when feature extraction is not needed.

    :param observation_space:
    """

    def __init__(self, observation_space: gym.Space):
        super(FlattenBatchNormDropoutExtractor, self).__init__(observation_space,
                                                               get_flattened_obs_dim(observation_space))
        self.flatten = nn.Flatten()
        self.batch_norm = nn.BatchNorm1d(self._features_dim)
        self.dropout = nn.Dropout(0.5)

    def forward(self, observations: th.Tensor) -> th.Tensor:
        result = self.flatten(observations)
        result = self.batch_norm(result)
        result = self.dropout(result)
        return result


def clone_batch_norm_stats(batch_norm: nn.BatchNorm1d) -> (th.Tensor, th.Tensor):
    """
    Clone the bias and running mean from the given batch norm layer.

    :param batch_norm:
    :return: the bias and running mean
    """
    return batch_norm.bias.clone(), batch_norm.running_mean.clone()


def clone_dqn_batch_norm_stats(model: DQN) -> (th.Tensor, th.Tensor, th.Tensor, th.Tensor):
    """
    Clone the bias and running mean from the Q-network and target network.

    :param model:
    :return: the bias and running mean from the Q-network and target network
    """
    q_net_batch_norm = model.policy.q_net.features_extractor.batch_norm
    q_net_bias, q_net_running_mean = clone_batch_norm_stats(q_net_batch_norm)

    q_net_target_batch_norm = model.policy.q_net_target.features_extractor.batch_norm
    q_net_target_bias, q_net_target_running_mean = clone_batch_norm_stats(q_net_target_batch_norm)

    return q_net_bias, q_net_running_mean, q_net_target_bias, q_net_target_running_mean


def clone_td3_batch_norm_stats(
        model: TD3,
) -> (th.Tensor, th.Tensor, th.Tensor, th.Tensor, th.Tensor, th.Tensor, th.Tensor, th.Tensor):
    """
    Clone the bias and running mean from the actor and critic networks and actor-target and critic-target networks.

    :param model:
    :return: the bias and running mean from the actor and critic networks and actor-target and critic-target networks
    """
    actor_batch_norm = model.policy.actor.features_extractor.batch_norm
    actor_bias, actor_running_mean = clone_batch_norm_stats(actor_batch_norm)

    critic_batch_norm = model.policy.critic.features_extractor.batch_norm
    critic_bias, critic_running_mean = clone_batch_norm_stats(critic_batch_norm)

    actor_target_batch_norm = model.policy.actor_target.features_extractor.batch_norm
    actor_target_bias, actor_target_running_mean = clone_batch_norm_stats(actor_target_batch_norm)

    critic_target_batch_norm = model.policy.critic_target.features_extractor.batch_norm
    critic_target_bias, critic_target_running_mean = clone_batch_norm_stats(critic_target_batch_norm)

    return (actor_bias, actor_running_mean, critic_bias, critic_running_mean,
            actor_target_bias, actor_target_running_mean, critic_target_bias, critic_target_running_mean)


@pytest.mark.parametrize("model_class", MODEL_LIST)
@pytest.mark.parametrize("env_id", ["Pendulum-v0", "CartPole-v1"])
def test_predict_with_dropout(model_class, env_id):
    if env_id == "CartPole-v1":
        if model_class in [SAC, TD3]:
            return
    elif model_class in [DQN]:
        return

    model_kwargs = dict(seed=1)

    if model_class in [DQN, TD3, SAC]:
        model_kwargs["learning_starts"] = 0
    else:
        model_kwargs["n_steps"] = 64

    policy_kwargs = dict(
        features_extractor_class=FlattenBatchNormDropoutExtractor,
        net_arch=[16, 16],
    )
    model = model_class("MlpPolicy", env_id, policy_kwargs=policy_kwargs, verbose=1, **model_kwargs)

    env = model.get_env()
    observation = env.reset()
    first_prediction, _ = model.predict(observation, deterministic=True)
    for _ in range(10):
        prediction, _ = model.predict(observation, deterministic=True)
        np.testing.assert_allclose(first_prediction, prediction)


def test_dqn_predict_with_batch_norm():
    model = DQN(
        "MlpPolicy",
        "CartPole-v1",
        policy_kwargs=dict(net_arch=[16, 16], features_extractor_class=FlattenBatchNormDropoutExtractor),
        seed=1,
    )

    (
        q_net_bias_before,
        q_net_running_mean_before,
        q_net_target_bias_before,
        q_net_target_running_mean_before,
    ) = clone_dqn_batch_norm_stats(model)

    env = model.get_env()
    observation = env.reset()
    for _ in range(10):
        model.predict(observation, deterministic=True)

    (
        q_net_bias_after,
        q_net_running_mean_after,
        q_net_target_bias_after,
        q_net_target_running_mean_after,
    ) = clone_dqn_batch_norm_stats(model)

    assert th.isclose(q_net_bias_before, q_net_bias_after).all()
    assert th.isclose(q_net_running_mean_before, q_net_running_mean_after).all()

    assert th.isclose(q_net_target_bias_before, q_net_target_bias_after).all()
    assert th.isclose(q_net_target_running_mean_before, q_net_target_running_mean_after).all()


def test_td3_predict_with_batch_norm():
    model = TD3(
        "MlpPolicy",
        "Pendulum-v0",
        policy_kwargs=dict(net_arch=[16, 16], features_extractor_class=FlattenBatchNormDropoutExtractor),
        seed=1,
    )

    (
        actor_bias_before,
        actor_running_mean_before,
        critic_bias_before,
        critic_running_mean_before,
        actor_target_bias_before,
        actor_target_running_mean_before,
        critic_target_bias_before,
        critic_target_running_mean_before,
    ) = clone_td3_batch_norm_stats(model)

    env = model.get_env()
    observation = env.reset()
    for _ in range(10):
        model.predict(observation, deterministic=True)

    (
        actor_bias_after,
        actor_running_mean_after,
        critic_bias_after,
        critic_running_mean_after,
        actor_target_bias_after,
        actor_target_running_mean_after,
        critic_target_bias_after,
        critic_target_running_mean_after,
    ) = clone_td3_batch_norm_stats(model)

    assert th.isclose(actor_bias_before, actor_bias_after).all()
    assert th.isclose(actor_running_mean_before, actor_running_mean_after).all()

    assert th.isclose(critic_bias_before, critic_bias_after).all()
    assert th.isclose(critic_running_mean_before, critic_running_mean_after).all()

    assert th.isclose(actor_target_bias_before, actor_target_bias_after).all()
    assert th.isclose(actor_target_running_mean_before, actor_target_running_mean_after).all()

    assert th.isclose(critic_target_bias_before, critic_target_bias_after).all()
    assert th.isclose(critic_target_running_mean_before, critic_target_running_mean_after).all()
