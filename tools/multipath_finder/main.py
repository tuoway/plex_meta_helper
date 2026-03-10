# -*- coding: utf-8 -*-
"""
[PMH Tool Reference Template] - 다중 경로(병합 오류 의심) 항목 검색 및 수정

* PMH Tool 아키텍처 핵심 가이드 (데이터테이블 반환형):
1. DB에서 조건에 맞는 데이터를 모두 조회하여 배열 형태로 반환하면, 
   코어와 프론트엔드가 페이징과 정렬을 알아서 처리합니다.
2. 프론트엔드에서 항목의 제목을 클릭했을 때 Plex 상세 페이지로 이동하게 하려면, 
   컬럼 속성에 `type: "link"` 와 `link_key: "데이터_키_이름"` 을 지정해주면 됩니다.
3. 시간이 오래 걸리는 조회 작업의 경우 `task.update_state('running', progress=..., total=...)` 
   를 호출해주면 프론트엔드 모니터링 탭에 파란색 진행률 바가 부드럽게 차오릅니다.
"""

import urllib.parse
import unicodedata
import os
import re

def is_season_folder(folder_name):
    """폴더명이 시즌(Season) 폴더인지 판별합니다."""
    name_lower = unicodedata.normalize('NFC', folder_name).lower().strip()
    if re.match(r'^(season|시즌|series|s)\s*\d+\b', name_lower): return True
    if re.match(r'^(specials?|스페셜|extras?|특집|ova|ost)(\s*\d+)?$', name_lower): return True
    if name_lower.isdigit(): return True
    return False

def get_unique_root_path(raw_file):
    """파일 경로를 받아, 시즌 폴더 등을 무시한 진짜 최상위(루트) 쇼/영화 폴더 경로를 반환합니다."""
    dir_path = os.path.dirname(raw_file)
    while True:
        base_name = os.path.basename(dir_path)
        if not base_name: break
        if is_season_folder(base_name):
            parent_path = os.path.dirname(dir_path)
            if parent_path == dir_path: break
            dir_path = parent_path
        else:
            break
    return os.path.normpath(dir_path).replace('\\', '/').lower()

def extract_movie_folder(filepath):
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

# =====================================================================
# 1. PMH Tool 표준 인터페이스 (UI 스키마)
# =====================================================================
def get_ui(core_api):
    sections = [{"value": "all", "text": "전체 라이브러리 (All)"}]
    try:
        # 안전한 샌드박스 DB 쿼리 실행 (코어가 제공하는 읽기 전용 쿼리)
        rows = core_api['query']("SELECT id, name FROM library_sections ORDER BY name")
        for r in rows:
            sections.append({"value": str(r['id']), "text": r['name']})
    except Exception:
        pass

    return {
        "title": "다중 경로(병합 오류 의심) 항목 검색 및 수정",
        "description": "서로 다른 폴더 경로를 가진 파일들이 하나의 메타로 병합된 항목을 찾거나 분리합니다. 분리 후 중복 GUID를 검색하여 수동 매칭을 할 수 있습니다.",
        "inputs": [
            {
                "id": "target_section", 
                "type": "select", 
                "label": "검사할 라이브러리 섹션", 
                "options": sections
            },
            {
                "id": "work_type",
                "type": "select",
                "label": "수행할 작업 선택",
                "options": [
                    {"value": "find_multipath", "text": "단순 조회: 잘못 병합된 다중 경로 항목 찾기"},
                    {"value": "split_multipath", "text": "[1단계] 풀기: 제목 폴더가 다른 데 묶인 경우 자동 분리 (Split)"},
                    {"value": "find_duplicate_guid", "text": "[2단계] 찾기: 제목과 GUID가 같은 중복 항목 검색 (링크 제공)"}
                ]
            }
        ],
        "button_text": "작업 실행"
    }

# =====================================================================
# 2. 메인 실행 및 데이터 추출 로직
# =====================================================================
def run(data, core_api):
    # 페이지/정렬 요청은 코어가 자체적으로 캐시를 읽어 처리하므로 예외를 던집니다.
    action = data.get('action_type', 'preview')
    if action == 'page': 
        return {"status": "error", "message": "데이터테이블 툴은 페이징을 코어가 전담합니다."}, 400

    section_id = data.get('target_section', 'all')
    work_type = data.get('work_type', 'find_multipath')
    
    task = core_api['task']
    task.log(f"작업 시작 (대상 섹션: {section_id}, 작업 유형: {work_type})")
    
    # [수정 포인트] 기기 식별자(Machine ID) 가져오기 (Plex Web URL 생성용)
    machine_id = ""
    try:
        plex = core_api['get_plex']()
        machine_id = plex.machineIdentifier
    except Exception as e:
        task.log(f"Plex 서버 연결 중 오류 (클릭 링크 생성이 제한될 수 있음): {e}")

    # -------------------------------------------------------------------------
    # [작업 1 & 2] 다중 경로 검색 및 분리(Split)
    # -------------------------------------------------------------------------
    if work_type in ['find_multipath', 'split_multipath']:
        query = """
            SELECT mi.id, mi.metadata_type, mi.title, ls.name AS section_name, ls.id AS sec_id
            FROM metadata_items mi
            JOIN library_sections ls ON mi.library_section_id = ls.id
            WHERE (? = 'all' OR ls.id = ?) AND mi.metadata_type IN (1, 2)
        """
        
        results = []
        try:
            task.log("1. 분석 대상 컨텐츠 목록 수집 중...")
            candidates = core_api['query'](query, (section_id, section_id))
            total_candidates = len(candidates)
            
            task.update_state('running', total=total_candidates)
            task.log(f"2. 총 {total_candidates:,}개의 컨텐츠 내부 파일 경로 분석 중...")
            
            targets_to_split = []

            for idx, candidate in enumerate(candidates, 1):
                if idx % 1000 == 0:
                    task.log(f"   -> {idx:,} / {total_candidates:,} 건 분석 완료...")
                if idx % 100 == 0:
                    task.update_state('running', progress=idx)
                    
                rk_id = candidate['id']
                m_type = candidate['metadata_type']
                title = candidate['title']
                sec_name = candidate['section_name']
                
                # [수정 포인트] 절대 URL 생성
                key_encoded = urllib.parse.quote(f"/library/metadata/{rk_id}", safe='')
                plex_web_url = f"https://app.plex.tv/desktop/#!/server/{machine_id}/details?key={key_encoded}" if machine_id else ""
                
                root_paths = set()
                
                # 영화 (Type 1)
                if m_type == 1:
                    files = core_api['query']("""
                        SELECT mp.file FROM media_items m 
                        JOIN media_parts mp ON mp.media_item_id = m.id 
                        WHERE m.metadata_item_id = ?
                    """, (rk_id,))
                    
                    for row in files:
                        if row.get('file'):
                            raw_file = unicodedata.normalize('NFC', row['file'])
                            root_paths.add(extract_movie_folder(raw_file))
                
                # TV 쇼 (Type 2)
                elif m_type == 2:
                    files = core_api['query']("""
                        SELECT mp.file FROM metadata_items ep 
                        JOIN metadata_items sea ON ep.parent_id = sea.id 
                        JOIN media_items m ON m.metadata_item_id = ep.id 
                        JOIN media_parts mp ON mp.media_item_id = m.id 
                        WHERE sea.parent_id = ? AND ep.metadata_type = 4
                    """, (rk_id,))
                    
                    for row in files:
                        if row.get('file'):
                            raw_file = unicodedata.normalize('NFC', row['file'])
                            root_paths.add(get_unique_root_path(raw_file))

                # 루트 폴더가 서로 다른 2개 이상이 묶여있다면 타겟으로 지정
                if len(root_paths) > 1:
                    item_data = {
                        "section": sec_name,
                        "title": title,
                        "rating_key": str(rk_id),
                        "plex_url": plex_web_url,  # 생성된 절대 링크 추가
                        "count": f"<span style='color:#e5a00d; font-weight:bold;'>{len(root_paths)}</span>",
                        "raw_count": len(root_paths),
                        "folders": ", ".join(root_paths)
                    }
                    results.append(item_data)
                    targets_to_split.append(item_data)
            
            # [자동 분리 수행]
            if work_type == 'split_multipath' and targets_to_split:
                task.log(f"3. 총 {len(targets_to_split)}개의 병합 의심 항목을 분리(Split) 합니다...")
                try:
                    plex = core_api['get_plex']()
                    split_count = 0
                    for split_idx, target in enumerate(targets_to_split, 1):
                        task.log(f"   -> [{split_idx}/{len(targets_to_split)}] 분리 시도: {target['title']}")
                        try:
                            item = plex.fetchItem(int(target['rating_key']))
                            item.split()
                            split_count += 1
                            target['count'] = "<span style='color:#28a745; font-weight:bold;'>분리 완료</span>"
                        except Exception as e:
                            task.log(f"   -> 분리 실패 ({target['title']}): {e}")
                            target['count'] = "<span style='color:#dc3545; font-weight:bold;'>분리 실패</span>"
                            
                    task.log(f"[완료] {split_count}개의 항목이 성공적으로 분리되었습니다.")
                except Exception as e:
                    task.log(f"Plex 서버 통신 오류 (Split 실패): {e}")

            task.update_state('running', progress=total_candidates)
            if work_type == 'find_multipath':
                task.log(f"검색 완료! {len(results):,}건의 다중 경로 항목을 찾았습니다.")
            
            return {
                "status": "success",
                "type": "datatable",
                "default_sort": [{"key": "section", "dir": "asc"}, {"key": "title", "dir": "asc"}],
                "columns": [
                    {"key": "section", "label": "섹션", "width": "15%", "align": "left", "header_align": "center", "sortable": True},
                    # [수정 포인트] link_key를 plex_url로 변경
                    {"key": "title", "label": "제목 (클릭 시 이동)", "width": "35%", "align": "left", "header_align": "center", "sortable": True, "type": "link", "link_key": "plex_url"},
                    {"key": "folders", "label": "감지된 폴더명", "width": "40%", "align": "left", "header_align": "center", "sortable": False},
                    {"key": "count", "label": "상태/병합수", "width": "10%", "align": "center", "header_align": "center", "sortable": True, "sort_key": "raw_count", "sort_type": "number"}
                ],
                "data": results
            }, 200

        except Exception as e:
            task.log(f"DB 검색 중 오류: {str(e)}")
            return {"status": "error", "message": f"DB 검색 중 오류: {str(e)}"}, 500

    # -------------------------------------------------------------------------
    # [작업 3] 동일 GUID 중복 항목 찾기
    # -------------------------------------------------------------------------
    elif work_type == 'find_duplicate_guid':
        task.log("1. 동일한 GUID를 가진 중복 항목을 검색합니다...")
        
        query = """
            SELECT mi.id, mi.title, mi.guid, ls.name AS section_name
            FROM metadata_items mi
            JOIN library_sections ls ON mi.library_section_id = ls.id
            WHERE (? = 'all' OR ls.id = ?) AND mi.metadata_type IN (1, 2)
            AND mi.guid IN (
                SELECT guid FROM metadata_items 
                WHERE metadata_type IN (1, 2) AND guid IS NOT NULL AND guid != ''
                GROUP BY guid HAVING COUNT(id) > 1
            )
            ORDER BY mi.guid, mi.title
        """
        
        try:
            duplicates = core_api['query'](query, (section_id, section_id))
            task.log(f"검색 완료! {len(duplicates):,}건의 중복(동일 GUID) 항목을 찾았습니다.")
            
            results = []
            for item in duplicates:
                rk_id = item['id']
                clean_guid = item['guid'].split("://")[-1].split("?")[0] if "://" in item['guid'] else item['guid']
                
                # [수정 포인트] 절대 URL 생성
                key_encoded = urllib.parse.quote(f"/library/metadata/{rk_id}", safe='')
                plex_web_url = f"https://app.plex.tv/desktop/#!/server/{machine_id}/details?key={key_encoded}" if machine_id else ""
                
                results.append({
                    "section": item['section_name'],
                    "title": item['title'],
                    "rating_key": str(rk_id),
                    "guid": clean_guid,
                    "plex_url": plex_web_url  # 생성된 절대 링크 추가
                })

            task.update_state('running', progress=100, total=100)

            return {
                "status": "success",
                "type": "datatable",
                "default_sort": [{"key": "guid", "dir": "asc"}, {"key": "title", "dir": "asc"}],
                "columns": [
                    {"key": "section", "label": "섹션", "width": "15%", "align": "left", "header_align": "center", "sortable": True},
                    # [수정 포인트] link_key를 plex_url로 변경
                    {"key": "title", "label": "제목 (클릭 시 이동)", "width": "50%", "align": "left", "header_align": "center", "sortable": True, "type": "link", "link_key": "plex_url"},
                    {"key": "guid", "label": "Plex GUID", "width": "35%", "align": "left", "header_align": "center", "sortable": True}
                ],
                "data": results
            }, 200

        except Exception as e:
            task.log(f"GUID 검색 중 오류: {str(e)}")
            return {"status": "error", "message": f"GUID 검색 중 오류: {str(e)}"}, 500
