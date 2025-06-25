# bam_compressor/models/lightweight_compression_model.py

import numpy as np

class SupervisedLayer:
    """
    NumPy 기반으로 구현된 Supervised Layer (SL) 또는 Bidirectional Associative Memory (BAM).
    MF 모듈에서 추출된 특성(h_l)과 최종 타겟(o)을 연관 짓는 역할을 합니다.

    - References:
        - Architecture: Fig. 3 (SL), Fig. 10
        - Learning Process: Fig. 7(b)
    """
    def __init__(self, feature_dim: int, target_dim: int, delta: float = 0.2):
        """
        Args:
            feature_dim (int): 입력 특성의 차원 (MF 모듈의 최종 출력 차원, m).
            target_dim (int): 타겟 데이터의 차원 (n).
            delta (float): Transmission function의 파라미터 (δ).
        """
        self.feature_dim = feature_dim
        self.target_dim = target_dim
        self.delta = delta
        self.reset_parameters()

    def reset_parameters(self):
        """ -0.1과 0.1 사이의 균등 분포로 가중치를 초기화합니다. """
        self.W = np.random.uniform(-0.1, 0.1, size=(self.target_dim, self.feature_dim))
        self.V = np.random.uniform(-0.1, 0.1, size=(self.feature_dim, self.target_dim))

    def transmission_function(self, activation: np.ndarray) -> np.ndarray:
        """ NumPy를 사용한 입방(cubic) 전달 함수. UL과 동일. """
        term1 = (self.delta + 1) * activation
        term2 = self.delta * np.power(activation, 3)
        output = term1 - term2
        return np.clip(output, a_min=-1.0, a_max=1.0)

    def forward(self, x_0: np.ndarray, y_0: np.ndarray, num_cycles: int = 1) -> tuple:
        """
        정보 순환 과정을 수행 (논문 그림 6b).
        x_0와 y_0를 동시에 입력받아 x_c와 y_c를 생성합니다.
        """
        x_current, y_current = x_0, y_0

        for _ in range(num_cycles):
            # 1. x[c] 계산: y_current가 V_SL을 통과하여 x_next를 만듦
            b_c = self.V @ y_current.T
            x_next = self.transmission_function(b_c).T
            
            # 2. y[c] 계산: x_current가 W_SL을 통과하여 y_next를 만듦
            a_c = self.W @ x_current.T
            y_next = self.transmission_function(a_c).T

            # 다음 순환을 위해 값 업데이트 (동시에 업데이트 되는 것을 모방)
            x_current, y_current = x_next, y_next
            
        return x_0, y_0, x_current, y_current # 최종 x_c, y_c 반환
    
    def predict(self, features: np.ndarray, num_cycles: int = 1) -> np.ndarray:
        """
        (추론/압축) 특성(features)을 입력받아 연관된 타겟을 예측합니다.
        
        Args:
            features (np.ndarray): MF 모듈로부터 온 특성 패턴.
            num_cycles (int): 안정된 출력을 얻기 위한 순환 횟수.

        Returns:
            np.ndarray: 예측된 타겟 값.
        """
        # 초기 y값은 없으므로 0으로 시작하거나, 첫 forward pass의 결과 사용
        a_0 = self.W @ features.T
        y_c = self.transmission_function(a_0).T
        
        x_c = features
        for _ in range(num_cycles):
            b_c = self.V @ y_c.T
            x_c = self.transmission_function(b_c).T
            
            a_c = self.W @ x_c.T
            y_c = self.transmission_function(a_c).T
        
        return y_c

    def update_weights(self, x_0, y_0, x_c, y_c, learning_rate):
        """ Hebbian/anti-Hebbian 학습 규칙에 따라 가중치를 업데이트합니다. """
        delta_W = (y_0 - y_c).T @ (x_0 + x_c)
        delta_V = (x_0 - x_c).T @ (y_0 + y_c)
        
        self.W += learning_rate * delta_W
        self.V += learning_rate * delta_V

# --- 사용 예시 ---
if __name__ == '__main__':
    # 설정값
    FEATURE_DIM = 20  # MF 모듈의 최종 출력 차원이라고 가정
    TARGET_DIM = 1    # Moons 데이터셋의 타겟 차원 (-1 또는 1)
    LEARNING_RATE = 0.01

    # SupervisedLayer (BAM) 인스턴스 생성
    sl_model = SupervisedLayer(feature_dim=FEATURE_DIM, target_dim=TARGET_DIM)
    
    print("Supervised Layer (BAM) 모델 생성 완료")
    print(f"Feature Dim: {sl_model.feature_dim}, Target Dim: {sl_model.target_dim}")
    print(f"W shape: {sl_model.W.shape}, V shape: {sl_model.V.shape}")

    # --- 단일 배치 학습 시뮬레이션 ---
    print("\n--- 단일 배치 학습 시뮬레이션 ---")
    
    # 임의의 입력 데이터 생성 (실제로는 MF 모듈의 출력과 데이터 로더의 타겟)
    # batch_size=4 라고 가정
    sample_features = np.random.randn(4, FEATURE_DIM)
    sample_targets = np.array([[-1.], [1.], [1.], [-1.]])

    print(f"입력 특성 (h_l) shape: {sample_features.shape}")
    print(f"입력 타겟 (o) shape: {sample_targets.shape}")
    
    # 학습에 필요한 변수 계산
    x0, y0, xc, yc = sl_model.forward(sample_features, sample_targets)
    
    # 가중치 업데이트
    sl_model.update_weights(x0, y0, xc, yc, LEARNING_RATE)
    print("\n가중치 업데이트 완료.")
    
    # MSE 손실 계산 (타겟에 대한)
    loss = np.mean((y0 - yc) ** 2)
    print(f"지도 학습 손실 (MSE on target): {loss:.6f}")
    
    # --- 추론 시뮬레이션 ---
    print("\n--- 추론(예측) 시뮬레이션 ---")
    
    # 새로운 특성 데이터에 대한 타겟 예측
    new_features = np.random.randn(1, FEATURE_DIM)
    predicted_target = sl_model.predict(new_features)
    
    print(f"새로운 특성 입력 shape: {new_features.shape}")
    print(f"예측된 타겟 shape: {predicted_target.shape}")
    print(f"예측된 타겟 값: {predicted_target}")
    
    # 논문에서는 보통 step function을 적용하여 최종 클래스를 결정
    final_class = np.sign(predicted_target)
    print(f"최종 분류 (sign 적용): {final_class}")