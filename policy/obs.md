针对 run `2026-08-27_14-43-05_motrix`（`run_config.json` 已确认：`empirical_normalization=true`，`action_scale=0.15`），整理如下。

---

## 1. 观测拼接代码

核心在 `MICROBANWalkEnv._compute_obs()`：

```407:463:src/unilab/envs/locomotion/microban/joystick.py
    def _compute_obs(
        self, info: dict, linvel, gyro, gravity, dof_pos, dof_vel
    ) -> dict[str, np.ndarray]:
        noise_cfg = self._cfg.noise_config
        diff = dof_pos - self.default_angles
        command = info["commands"]
        last_actions = info.get("current_actions", np.zeros_like(diff))
        gait_phase = info.get("gait_phase", np.zeros((self._num_envs, 2), dtype=get_global_dtype()))
        walk_profile = self._uses_walk_observation_profile()

        noisy_gyro = self._obs_noise(gyro, noise_cfg.scale_gyro)
        noisy_gravity = self._obs_noise(gravity, noise_cfg.scale_gravity)
        noisy_diff = self._obs_noise(diff, noise_cfg.scale_joint_angle)
        noisy_dof_vel = self._obs_noise(dof_vel, noise_cfg.scale_joint_vel)
        actor_gyro_scale = 0.25 if walk_profile else 1.0
        actor_dof_vel_scale = 0.05 if walk_profile else 1.0

        actor = np.concatenate(
            [
                noisy_gyro * actor_gyro_scale,
                -noisy_gravity,
                noisy_diff,
                noisy_dof_vel * actor_dof_vel_scale,
                last_actions,
                command,
                gait_phase,
            ],
            axis=1,
            dtype=get_global_dtype(),
        )
        # ... critic 分支省略 ...
        return {"obs": actor, "critic": critic}
```

传感器来源（`update_state`）：

```370:375:src/unilab/envs/locomotion/microban/joystick.py
        linvel = self.get_local_linvel()
        gyro = self.get_gyro()
        gravity = self._backend.get_sensor_data(self._cfg.sensor.upvector)
        dof_pos = self.get_dof_pos()
        dof_vel = self.get_dof_vel()
```

对应 `Sensor` 配置（`base.py`）→ XML 传感器名：

| 变量 | 传感器名 | XML 挂载 site |
|------|----------|---------------|
| `gyro` | `trunk_gyro` | `body`（躯干原点） |
| `gravity` | `trunk_upvector` | `body` |
| `linvel`（仅 critic） | `trunk_local_linvel` | `body` |

---

## 2. Actor 观测 47 维布局（该 run 的实际缩放）

该 run 的 reward scales 含 `orientation / ang_vel_xy / action_rate`，因此 **`walk_profile = False`**，gyro/dof_vel **不做 0.25/0.05 缩放**。

| 索引 | 维度 | 内容 | 该 run 处理 |
|------|------|------|-------------|
| 0–2 | 3 | 角速度 `trunk_gyro` | `+ noise(×0.2)`，×**1.0** |
| 3–5 | 3 | 重力方向 | `-trunk_upvector`，`+ noise(×0.05)` |
| 6–17 | 12 | `dof_pos - default_angles` | `+ noise(×0.01)` |
| 18–29 | 12 | `dof_vel` | `+ noise(×1.5)`，×**1.0** |
| 30–41 | 12 | 上一步 action（`current_actions`） | 无噪声 |
| 42–44 | 3 | command `[vx, vy, ωz]` | 无噪声 |
| 45–46 | 2 | gait_phase `[φ_L, φ_R]` (rad) | 无噪声 |

**关节 / action 顺序**（与 actuator 一致，右腿在前）：

```
right_hip_yaw, right_hip_roll, right_hip_pitch, right_knee,
right_ankle_pitch, right_ankle_roll,
left_hip_yaw, left_hip_roll, left_hip_pitch, left_knee,
left_ankle_pitch, left_ankle_roll
```

**动作后处理**（部署需一致）：

\[
q_{\text{target}} = a \times 0.15 + q_{\text{default}}
\]

其中 `q_default` 来自 keyframe `stand` 的 ctrl（全 0，即 CAD 零位站立姿）。

**Command 范围**（训练采样，`vel_limit`）：

```
vx: [0.1, 0.40],  vy: [0.0, 0.0],  ωz: [-0.1, 0.1]
```

死区 `command_threshold=0.1` 内视为站立，command 清零、gait_phase 冻结。

---

## 3. ONNX input shape / metadata

导出路径：`scripts/train_rsl_rl.py` play 时调用 `runner.export_policy_to_onnx()`，写入 run 目录。该 run 下 **`policy.onnx` 已存在**。

RSL-RL 5.0.1 的 ONNX 封装（`_OnnxMLPModel`）规范：

| 字段 | 值 |
|------|-----|
| **input name** | `"obs"` |
| **input shape** | `(N, 47)`，trace 时用 `(1, 47)` |
| **output name** | `"actions"` |
| **output shape** | `(N, 12)` |
| **opset** | 18 |
| **normalization** | `empirical_normalization=true` → **Normalizer 已 bake 进图内**，部署侧喂**原始 47 维**（与训练拼接一致，**不加训练噪声**） |
| **output 语义** | Gaussian policy 的 **deterministic mean**（非采样） |

该 run 的 `run_config.json` 关键字段：

```json
"empirical_normalization": true,
"env.control_config.action_scale": 0.15,
"obs_groups": { "actor": ["actor"], "critic": ["critic"] }
```

**注意**：ONNX 图本身**不含** obs 布局 metadata（无 `deploy_config.yaml` 那种 segment 描述）；布局需按上文 47 维表手动对齐。Critic 的 50 维（含 linvel×1.0）**不会**导出到 policy ONNX。

---

## 4. 训练模型中 IMU 的安装位置

XML 里有两套 site，用途不同：

### A. 策略实际使用的传感器 — `body` site（躯干原点）

```65:67:src/unilab/assets/robots/microban/microban.xml
      <site group="3" name="body" pos="0 0 0" quat="1 -0 -0 -0"/>
      <!-- Frame imu -->
      <site group="3" name="imu" pos="0.01483 -0.002 0.0675" quat="0.5 -0.5 -0.5 0.5"/>
```

```38:40:src/unilab/assets/robots/microban/microban.xml
    <velocimeter site="body" name="trunk_local_linvel"/>
    <gyro site="body" name="trunk_gyro"/>
    <framezaxis objtype="site" objname="body" name="trunk_upvector"/>
```

**训练 policy 的 gyro / gravity 来自 `body` site（trunk 坐标系原点），不是 `imu` site。**

### B. 物理 IMU 建模 site — `imu` site（仅 XML 定义，未进 policy obs）

- **父 body**：`trunk`
- **相对 trunk 原点位置**：`(0.01483, -0.002, 0.0675)` m → **x=+14.8 mm，y=-2 mm，z=+67.5 mm**（约在躯干上部/胸廓区域）
- **姿态**：`quat="0.5 -0.5 -0.5 0.5"`（相对 trunk 有 90° 轴旋转）
- **挂载传感器**：`imu_ang_vel`、`imu_lin_vel`、`imu_accel`、`orientation`（framequat）

这些 **`imu_*` 传感器未接入 `_compute_obs()`**，当前 walk 策略观测与 **`body` 原点 IMU 等效**，而非真实 IMU 安装点。

---

## 5. 部署对齐 checklist

1. 按 47 维表拼接 obs（**无训练噪声**）
2. ONNX 输入 `(1, 47)` → 输出 `(1, 12)`
3. `q_target = action × 0.15 + default_angles`
4. 自研 IMU 若在 `(14.8, -2, 67.5) mm` 且 frame 与 XML `imu` quat 一致，需 **变换到 trunk 原点 frame** 才能与训练分布对齐；否则 sim-to-real 会有系统偏差

步态相位 **不是从足端接触或关节角反推的**，而是一个 **开环、固定频率的虚拟时钟**，由 env 在 `info["gait_phase"]` 里维护，再直接拼进观测的最后 2 维。

---

## 数学定义

记左右腿相位为 \(\phi_L, \phi_R \in [0, 2\pi)\)，每控制步更新：

\[
\begin{bmatrix} \phi_L \\ \phi_R \end{bmatrix}
\leftarrow
\left(
\begin{bmatrix} \phi_L \\ \phi_R \end{bmatrix}
+
\Delta\phi \cdot \mathbf{1}_{\|\mathbf{cmd}\| > \tau}
\right) \bmod 2\pi
\]

其中：

- \(\Delta\phi = 2\pi \cdot f_{\text{gait}} \cdot \Delta t\)
- \(f_{\text{gait}}\) = `reward.gait_frequency`（你的 run 为 **1.4 Hz**）
- \(\Delta t\) = `ctrl_dt`（Microban 为 **0.02 s**）
- \(\tau\) = `command_threshold`（**0.1**）
- \(\mathbf{cmd} = [v_x, v_y, \omega_z]\)

代入你的 run：

\[
\Delta\phi = 2\pi \times 1.4 \times 0.02 \approx 0.176 \text{ rad/step} \approx 10.1°/\text{step}
\]

---

## 代码流程

### 1. Reset 时初始化

默认 `gait_phase_init_mode = "offset_phase"`（左右腿相差 \(\pi\)，标准交替步态）：

```260:268:src/unilab/envs/locomotion/microban/joystick.py
    def _sample_gait_phase(self, env: Any, num_reset: int) -> np.ndarray:
        mode = env.cfg.gait_phase_init_mode
        if mode == "independent":
            left = np.random.uniform(0.0, 2.0 * np.pi, size=(num_reset,))
            right = np.random.uniform(0.0, 2.0 * np.pi, size=(num_reset,))
            return np.asarray(np.column_stack([left, right]), dtype=get_global_dtype())

        phase = np.random.uniform(0.0, 2.0 * np.pi, size=(num_reset,))
        return np.asarray(np.column_stack([phase, phase + np.pi]), dtype=get_global_dtype())
```

即：

\[
\phi_L \sim U[0, 2\pi),\quad \phi_R = \phi_L + \pi \pmod{2\pi}
\]

若 `mode == "independent"`，左右腿各自独立均匀采样。

### 2. 每步在 `apply_action` 里推进

```305:307:src/unilab/envs/locomotion/microban/joystick.py
        self._gait_phase_delta = float(
            2.0 * math.pi * self._reward_cfg.gait_frequency * cfg.ctrl_dt
        )
```

```652:666:src/unilab/envs/locomotion/microban/joystick.py
    def apply_action(self, actions: np.ndarray, state: NpEnvState) -> np.ndarray:
        ...
        moving = compute_gait_command_gate(
            state.info.get("commands", ...),
            self._cfg.command_threshold,
        )
        gait_phase = (gait_phase + self._gait_phase_delta * moving[:, None]) % (2 * np.pi)
        state.info["gait_phase"] = gait_phase
```

`moving` 判定：

```131:133:src/unilab/envs/locomotion/microban/joystick.py
def compute_gait_command_gate(commands: np.ndarray, threshold: float) -> np.ndarray:
    """Enable phase-driven gait behavior only for commands outside the stand deadzone."""
    return np.asarray(np.linalg.norm(commands, axis=1) > threshold, dtype=get_global_dtype())
\]

**站立时**（\(\|\mathbf{cmd}\| \le 0.1\)）：相位 **冻结**，不再递增。测试也验证了这一点。

### 3. 拼进观测

```414:432:src/unilab/envs/locomotion/microban/joystick.py
        gait_phase = info.get("gait_phase", np.zeros((self._num_envs, 2), dtype=get_global_dtype()))
        ...
        actor = np.concatenate(
            [
                ...
                command,
                gait_phase,   # obs[45:47] = [φ_L, φ_R]
            ],
```

观测里是直接喂 **原始弧度值**，没有 `sin/cos` 编码。

---

## 相位在 reward 里的语义（与 obs 共用同一变量）

同一 `gait_phase` 还通过 Bezier 曲线映射成左右脚 **期望摆动高度**，用于 `feet_phase` 等 reward：

```73:95:src/unilab/envs/locomotion/microban/joystick.py
def compute_feet_phase_height_targets(gait_phase, swing_height):
    # φ 归一化到 [-π, π)，再映射到 [0,1]
    phi_normalized = np.fmod(phi + np.pi, 2 * np.pi) - np.pi
    x = (phi_normalized + np.pi) / (2 * np.pi)
    # x ∈ [0, 0.5]: 支撑→抬脚;  x ∈ (0.5, 1]: 摆荡→落脚
```

直观理解一个周期（以单腿为例）：

| 相位区间 | 含义 |
|----------|------|
| \(\phi \in [0, \pi)\) | 支撑相 → 抬脚（高度 0 → `swing_height`） |
| \(\phi \in [\pi, 2\pi)\) | 摆动相 → 落脚（高度 `swing_height` → 0） |

左右腿初始相差 \(\pi\)，所以一腿支撑时另一腿摆动。

---

## 部署侧注意

1. **必须自己维护** `gait_phase` 状态，不能指望 IMU/关节反推。
2. 用同一公式推进：\(\phi \leftarrow (\phi + \Delta\phi \cdot \mathbb{1}_{moving}) \bmod 2\pi\)。
3. `moving` 判定要与训练一致：command 范数 **> 0.1** 才推进。
4. Reset 时建议用 `offset_phase` 初始化（\(\phi_R = \phi_L + \pi\)），与训练分布一致。
5. 相位是 **理想时钟**，与实际足端触地可能不同步；策略学的是「跟着这个时钟走」。

如果你需要，我可以再写一段可直接用于 C++/Python 部署的 `gait_phase` 更新伪代码。