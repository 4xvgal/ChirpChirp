# bam_compressor/models/lightweight_feature_extractor.py
import numpy as np
import os
class UnsupervisedLayer:
    def __init__(self, input_dim: int, hidden_dim: int, delta: float = 0.2):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.delta = delta
        self.reset_parameters()

    def reset_parameters(self):
        init_range = 0.05 # 안정성을 위해 초기화 범위 약간 줄임
        self.W = np.random.uniform(-init_range, init_range, size=(self.hidden_dim, self.input_dim))
        self.V = np.random.uniform(-init_range, init_range, size=(self.input_dim, self.hidden_dim))

    def transmission_function(self, activation: np.ndarray) -> np.ndarray:
        term1 = (self.delta + 1) * activation
        term2 = self.delta * np.power(activation, 3)
        output = term1 - term2
        return np.clip(output, a_min=-1.0, a_max=1.0)

    def forward(self, x_0: np.ndarray, num_cycles: int = 1) -> tuple: # 학습 시 사용
        a_0 = self.W @ x_0.T
        y_0 = self.transmission_function(a_0).T
        y_c = y_0
        for _ in range(num_cycles):
            b_c = self.V @ y_c.T
            x_c = self.transmission_function(b_c).T
            a_c = self.W @ x_c.T
            y_c = self.transmission_function(a_c).T
        return x_0, y_0, x_c, y_c
    
    def update_weights(self, x_0, y_0, x_c, y_c, learning_rate):
        delta_W = (y_0 - y_c).T @ (x_0 + x_c)
        delta_V = (x_0 - x_c).T @ (y_0 + y_c)
        self.W += learning_rate * delta_W
        self.V += learning_rate * delta_V

    def predict(self, x_in: np.ndarray) -> np.ndarray: # 추론 시 사용
        a = self.W @ x_in.T
        y_out = self.transmission_function(a).T
        return y_out

class FeatureExtractor:
    def __init__(self, layer_dims: list[int], delta: float = 0.2):
        self.layers = []
        for i in range(len(layer_dims) - 1):
            self.layers.append(
                UnsupervisedLayer(layer_dims[i], layer_dims[i+1], delta)
            )

    def predict(self, p: np.ndarray) -> np.ndarray:
        h = p
        for layer in self.layers:
            h = layer.predict(h)
        return h

    # ▼▼▼▼▼▼▼▼▼▼▼ 이 두 메서드를 추가합니다 ▼▼▼▼▼▼▼▼▼▼▼
    def save_weights(self, path: str):
        """ MF 모듈의 모든 레이어 가중치를 파일로 저장합니다. """
        os.makedirs(path, exist_ok=True) # 저장할 폴더가 없으면 생성
        for i, layer in enumerate(self.layers):
            # 각 레이어의 가중치를 mf_layer_X_weights.npz 형태로 저장
            np.savez(os.path.join(path, f'mf_layer_{i}_weights.npz'), W=layer.W, V=layer.V)
        print(f"FeatureExtractor 가중치가 '{path}' 디렉토리에 저장되었습니다.")

    def load_weights(self, path: str):
        """ 파일에서 MF 모듈의 모든 레이어 가중치를 불러옵니다. """
        try:
            for i, layer in enumerate(self.layers):
                weight_file = os.path.join(path, f'mf_layer_{i}_weights.npz')
                if not os.path.exists(weight_file):
                    raise FileNotFoundError(f"가중치 파일 없음: {weight_file}")
                data = np.load(weight_file)
                layer.W = data['W']
                layer.V = data['V']
            print(f"FeatureExtractor 가중치를 '{path}' 디렉토리에서 불러왔습니다.")
        except FileNotFoundError as e:
            # 파일이 없을 경우 초기화된 가중치를 그대로 사용하거나, 에러 발생
            print(f"경고: 가중치 파일을 찾을 수 없어 로드하지 못했습니다. ({e}) 초기화된 가중치를 사용합니다.")
            # raise e # 로드 실패 시 프로그램을 중단시키려면 이 라인 주석 해제
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
# --- 사용 예시 ---
if __name__ == '__main__':
    # 설정
    layer_dims = [10, 30, 50, 20]  # 입력 10 -> UL1(30) -> UL2(50) -> UL3(20)
    
    print(f"경량화된 특성 추출 모듈(MF) 생성: {' -> '.join(map(str, layer_dims))}")
    feature_extractor = FeatureExtractor(layer_dims=layer_dims)
    
    # 임의의 입력 데이터 및 학습 설정
    input_data = np.random.randn(1, layer_dims[0])
    learning_rate = 1e-4  # 학습률 설정

    print(f"\n입력 데이터 (p) shape: {input_data.shape}")

    # --- 계층별 순차 학습 시뮬레이션 ---
    print("\n--- 계층별 학습 과정 (시뮬레이션) ---")
    current_input = input_data
    for i, layer in enumerate(feature_extractor.layers):
        print(f"\nLayer {i+1} 학습 중...")
        # 해당 계층에 대한 학습 변수 계산
        x0, y0, xc, yc = layer.forward(current_input)
        
        # 가중치 업데이트
        layer.update_weights(x0, y0, xc, yc, learning_rate)
        
        # 다음 계층의 입력은 현재 계층의 출력이 됨
        current_input = yc
        print(f"Layer {i+1}의 출력 shape (다음 Layer의 입력): {current_input.shape}")

    # --- 추론 시뮬레이션 ---
    print("\n\n--- 전체 모듈 추론 과정 ---")
    final_features = feature_extractor.predict(input_data)
    print(f"최종 특성맵 (h_l) shape: {final_features.shape}")