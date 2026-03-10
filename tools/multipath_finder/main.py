import os
import re
import urllib.parse
from plexapi.server import PlexServer

class PlexMultipathManager:
    def __init__(self, plex_url, plex_token):
        """
        Plex 서버 객체를 초기화하고 머신 식별자를 가져옵니다.
        """
        self.plex_server = PlexServer(plex_url, plex_token)
        self.machine_id = self.plex_server.machineIdentifier

    def extract_movie_folder(self, filepath):
        """
        주어진 경로에서 실제 영화 폴더명을 추출합니다.
        기준 경로 패턴: /영화/제목/[가~0Z 등 임의의폴더]/[실제_영화_폴더명]/
        """
        # 사용자 맞춤형 정규식: /영화/제목/ 다음에 오는 1뎁스 폴더를 건너뛰고 그 다음 폴더명을 캡처
        match = re.search(r'/영화/제목/[^/]+/([^/]+)/', filepath)
        if match:
            return match.group(1)
        
        # 위 패턴에 맞지 않는 경우, 기본적으로 파일이 위치한 직속 부모 폴더명을 반환
        return os.path.basename(os.path.dirname(filepath))

    def split_incorrectly_merged_movies(self, library_name):
        """
        1. 제목 폴더가 다른 데 묶인 경우 풀기 (Split)
        하나의 영화 항목 내에 존재하는 파일들이 서로 다른 폴더에 위치한 경우 분리합니다.
        """
        print(f"\n['{library_name}' 라이브러리: 잘못 병합된 항목 스캔 시작]")
        try:
            library = self.plex_server.library.section(library_name)
            movies = library.all()
        except Exception as e:
            print(f"라이브러리를 불러오는 데 실패했습니다: {e}")
            return

        split_count = 0

        for movie in movies:
            # 항목 내 모든 실제 물리 파일 경로 수집
            file_locations = []
            for media in movie.media:
                for part in media.parts:
                    if part.file:
                        file_locations.append(part.file)
            
            # 묶여 있는 파일이 2개 이상일 때만 검사
            if len(file_locations) < 2:
                continue
                
            # 각 파일이 위치한 실제 영화 폴더명 추출 및 중복 제거
            movie_folders = set()
            for filepath in file_locations:
                folder_name = self.extract_movie_folder(filepath)
                movie_folders.add(folder_name)
            
            # 폴더명이 2개 이상이라면 서로 다른 영화가 병합된 것으로 간주
            if len(movie_folders) > 1:
                print(f"\n[분리 대상 발견] {movie.title} (ID: {movie.ratingKey})")
                print(f"  - 감지된 독립 폴더들: {', '.join(movie_folders)}")
                
                try:
                    movie.split()
                    split_count += 1
                    print(f"  -> 성공적으로 분리(Split) 되었습니다.")
                except Exception as e:
                    print(f"  -> 분리 실패: {e}")
                    
        print(f"\n[스캔 완료] 총 {split_count}개의 항목이 분리되었습니다.")

    def find_duplicate_guid_movies(self, library_name):
        """
        2. 제목이 같고 GUID가 같은 경우 찾기
        동일한 GUID를 공유하는 중복 항목들을 찾아 웹에서 즉시 수정할 수 있는 링크를 제공합니다.
        """
        print(f"\n['{library_name}' 라이브러리: 중복 GUID 항목 스캔 시작]")
        try:
            library = self.plex_server.library.section(library_name)
            movies = library.all()
        except Exception as e:
            print(f"라이브러리를 불러오는 데 실패했습니다: {e}")
            return

        # GUID를 키로 하여 영화 객체들을 리스트로 그룹화
        guid_map = {}
        for movie in movies:
            guid = movie.guid
            if guid not in guid_map:
                guid_map[guid] = []
            guid_map[guid].append(movie)
            
        found_duplicates = False
        
        for guid, items in guid_map.items():
            # 동일한 GUID를 가진 항목이 2개 이상인 경우 (중복)
            if len(items) > 1:
                found_duplicates = True
                print(f"\n[중복 GUID 그룹] {guid}")
                
                for item in items:
                    # Plex Web 링크 생성을 위한 URL 인코딩
                    key_encoded = urllib.parse.quote(item.key, safe='')
                    plex_web_url = f"https://app.plex.tv/desktop/#!/server/{self.machine_id}/details?key={key_encoded}"
                    
                    # 출력용 파일 경로 추출 (첫 번째 파일 기준)
                    file_path = "경로 없음"
                    if item.media and item.media[0].parts and item.media[0].parts[0].file:
                        file_path = item.media[0].parts[0].file
                    
                    print(f"  - 제목: {item.title} (ID: {item.ratingKey})")
                    print(f"    경로: {file_path}")
                    print(f"    수정 링크: {plex_web_url}")
                    
        if not found_duplicates:
            print("\n중복된 GUID를 가진 항목이 없습니다. 모두 정상입니다.")

def main():
    """
    메인 실행 함수: CLI 메뉴를 구성하고 사용자의 입력을 처리합니다.
    """
    # 환경 변수 또는 하드코딩으로 Plex 접속 정보를 입력하세요.
    PLEX_URL = os.environ.get('PLEX_URL', 'http://localhost:32400')
    PLEX_TOKEN = os.environ.get('PLEX_TOKEN', 'YOUR_PLEX_TOKEN_HERE')
    LIBRARY_NAME = '영화'

    print("=== Plex 매칭 오류 수정 도구 ===")
    print("Plex 서버에 연결 중입니다...")
    
    try:
        manager = PlexMultipathManager(PLEX_URL, PLEX_TOKEN)
        print("서버에 성공적으로 연결되었습니다.")
    except Exception as e:
        print(f"서버 연결 실패: {e}")
        return
    
    while True:
        print(f"\n[현재 타겟 라이브러리: {LIBRARY_NAME}]")
        print("1. 잘못 병합된 항목 자동 분리 (폴더명 기준)")
        print("2. GUID 중복 항목 검색 및 수동 수정 링크 보기")
        print("0. 프로그램 종료")
        
        choice = input("원하시는 작업 번호를 입력하세요: ")
        
        if choice == '1':
            manager.split_incorrectly_merged_movies(LIBRARY_NAME)
        elif choice == '2':
            manager.find_duplicate_guid_movies(LIBRARY_NAME)
        elif choice == '0':
            print("작업을 종료합니다.")
            break
        else:
            print("잘못된 입력입니다. 0, 1, 2 중 하나를 입력해주세요.")

if __name__ == "__main__":
    main()
