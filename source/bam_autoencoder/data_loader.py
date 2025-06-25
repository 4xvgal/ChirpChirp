# bam_compressor/data/data_loader.py

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler

class NumpyDataLoader:
    """
    파일에서 데이터를 로드하고, NumPy 배열 형태로 미니배치를 제공하는 경량 데이터 로더.
    """
    def __init__(self, filepath: str, batch_size: int, test_size: float = 0.2, shuffle: bool = True, random_state: int = 42):
        """
        Args:
            filepath (str): 데이터 파일의 경로 (e.g., 'data/original/moons_dataset.csv').
            batch_size (int): 각 배치에 포함될 데이터 샘플의 수.
            test_size (float): 전체 데이터에서 테스트 데이터셋으로 분리할 비율.
            shuffle (bool): 매 에폭마다 학습 데이터를 섞을지 여부.
            random_state (int): 데이터 분할 및 셔플링의 재현성을 위한 시드값.
        """
        self.filepath = filepath
        self.batch_size = batch_size
        self.shuffle = shuffle
        
        # 1. 데이터를 먼저 로드하고 self.X, self.y를 생성합니다.
        self._load_and_preprocess_data()
        
        # 2. 생성된 self.X, self.y를 사용하여 데이터를 분할합니다.
        self._split_data(test_size, random_state)
        
        self._current_index = 0

    def _load_and_preprocess_data(self):
        """ CSV 파일을 읽고, 모든 데이터를 피처(X)로 사용하며, 스케일링을 수행합니다. """
        try:
            df = pd.read_csv(self.filepath)
        except FileNotFoundError:
            raise FileNotFoundError(f"데이터 파일을 찾을 수 없습니다: {self.filepath}\n"
                                    f"솔루션: 'scripts/preprocess_lora_log.py'를 먼저 실행하여 데이터를 정제해주세요.")
        
        # 모든 열을 피처(X)로 사용
        self.feature_names = df.columns.tolist() # 나중에 시각화를 위해 컬럼 이름 저장
        raw_data = df.values
        
        # Min-Max Scaler를 생성하고 학습(fit)
        self.scaler = MinMaxScaler(feature_range=(-1, 1))
        # 스케일링된 데이터를 self.X에 할당
        self.X = self.scaler.fit_transform(raw_data)
        
        # 오토인코더의 타겟(y)은 입력(X)과 동일합니다.
        self.y = self.X.copy()
        
        print(f"데이터 로드 및 스케일링 완료. 총 샘플: {len(self.X)}, 피처 수: {self.X.shape[1]}")


    def _split_data(self, test_size: float, random_state: int):
        """ 데이터를 학습용과 테스트용으로 분리합니다. """
        if test_size > 0 and len(self.X) > 1:
            self.X_train, self.X_test, self.y_train, self.y_test = train_test_split(
                self.X, self.y, test_size=test_size, random_state=random_state
            )
        else:
            self.X_train, self.y_train = self.X, self.y
            self.X_test, self.y_test = np.array([]), np.array([])
            
        self.n_samples = len(self.X_train)
        self.n_batches = int(np.ceil(self.n_samples / self.batch_size))
        
        print(f"학습 데이터: {len(self.X_train)} 개, 테스트 데이터: {len(self.X_test)} 개")
        print(f"배치 크기: {self.batch_size}, 총 배치 수: {self.n_batches}")
    def __iter__(self):
        """ 반복자(iterator)를 초기화합니다. 에폭 시작 시 호출됩니다. """
        self._current_index = 0
        if self.shuffle:
            # 학습 데이터의 인덱스를 섞음
            indices = np.arange(self.n_samples)
            np.random.shuffle(indices)
            self.X_train = self.X_train[indices]
            self.y_train = self.y_train[indices]
        return self

    def __next__(self):
        """ 다음 배치를 반환합니다. """
        if self._current_index >= self.n_samples:
            # 모든 데이터를 순회했으면 반복 종료
            raise StopIteration

        start = self._current_index
        end = start + self.batch_size
        self._current_index = end
        
        # 마지막 배치가 batch_size보다 작을 수 있음
        batch_X = self.X_train[start:end]
        batch_y = self.y_train[start:end]
        
        return batch_X, batch_y

    def get_test_data(self):
        """ 테스트 데이터셋 전체를 반환합니다. """
        return self.X_test, self.y_test
    
    # 디코딩된 결과를 원래 값의 범위로 되돌리기 위한 메서드
    def inverse_transform(self, scaled_data: np.ndarray) -> np.ndarray:
        """ 스케일링된 데이터를 원래의 값 범위로 복원합니다. """
        return self.scaler.inverse_transform(scaled_data)

# --- 사용 예시 ---
if __name__ == '__main__':
    # 데이터 로더 인스턴스 생성
    # batch_size=1은 온라인 학습(샘플 하나씩 학습)과 유사
    data_loader = NumpyDataLoader(
        filepath='data/original/moons_dataset.csv',
        batch_size=16,
        shuffle=True
    )

    # 1 에폭(epoch) 동안 데이터 로더 사용하기
    print("\n--- 1 에폭 학습 시뮬레이션 ---")
    num_epochs = 2
    for epoch in range(num_epochs):
        print(f"\n[ Epoch {epoch+1}/{num_epochs} ]")
        # for-loop를 사용하면 내부적으로 __iter__와 __next__가 호출됨
        for i, (batch_X, batch_y) in enumerate(data_loader):
            print(f"  - Batch {i+1}/{data_loader.n_batches}: X shape {batch_X.shape}, y shape {batch_y.shape}")
            # 이 곳에서 모델 학습 로직이 수행됩니다.
            # model.train(batch_X, batch_y)

    # 테스트 데이터 가져오기
    X_test, y_test = data_loader.get_test_data()
    print(f"\n--- 테스트 데이터 ---")
    print(f"X_test shape: {X_test.shape}, y_test shape: {y_test.shape}")