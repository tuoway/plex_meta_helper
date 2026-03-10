# -*- coding: utf-8 -*-
"""
[PMH Tool Reference Template] - 다중 경로(병합 오류 의심) 항목 검색 및 수정

* PMH Tool 아키텍처 핵심 가이드 (데이터테이블 반환형):
1. DB에서 조건에 맞는 데이터를 모두 조회하여 배열 형태로 반환하면, 
   코어와 프론트엔드가 페이징과 정렬을 알아서 처리합니다.
2. 시간이 오래 걸리는 조회 작업의 경우 `task.update_state('running', progress=..., total=...)` 
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
    match = re.search(r'/영화/제목/[^/]+/([^/]+)/', filepath)
    if match:
        return match.group(1)
    
    return os.path.basename(os.path.dirname(filepath))

# =====================================================================
# 1. PMH Tool 표준 인터페이스 (UI 스키마)
# =====================================================================
def get_ui(core_api):
    sections = [{"value": "all", "text": "전체 라이브러리 (All)"}]
    try:
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
                "label": "수행할 기본 작업 선택",
                "options": [
                    {"value": "find_multipath", "text": "단순 조회: 잘못 병합된 다중 경로 항목 찾기"},
                    {"value": "split_multipath", "text": "[1단계] 일괄 풀기: 제목 폴더가 다른 데 묶인 항목 모두 자동 분리"},
                    {"value": "find_duplicate_guid", "text": "[2단계] 찾기: 제목과 GUID가 같은 중복 항목 검색"}
                ]
            },
            {
                "id": "target_rk",
                "type": "text",
                "label": "🚀 콕 집어서 수동 분리 (ID를 넣고 실행하면, 분리 후 표가 날아가지 않고 새로고침 됩니다!)",
                "placeholder": "표에 있는 ID (RK) 숫자 입력 -> 그대로 [작업 실행] 클릭"
            }
        ],
        "button_text": "작업 실행"
    }

# =====================================================================
# 2. 메인 실행 및 데이터 추출 로직
# =====================================================================
def run(data, core_api):
    action = data.get('action_type', 'preview')
    if action == 'page': 
        return {"status": "error", "message": "데이터테이블 툴은 페이징을 코어가 전담합니다."}, 400

    section_id = data.get('target_section', 'all')
    work_type = data.get('work_type', 'find_multipath')
    target_rk = data.get('target_rk', '').strip()
    
    task = core_api['task']
    task.log(f"작업 시작 (대상 섹션: {section_id}, 기본 작업: {work_type})")
    
    # 기기 식별자(Machine ID) 가져오기
    machine_id = ""
    try:
        plex = core_api['get_plex']()
        machine_id = plex.machineIdentifier
    except Exception as e:
        task.log(f"Plex 서버 연결 중 오류 (클릭 링크 생성이 제한될 수 있음): {e}")

    # -------------------------------------------------------------------------
    # [우선 실행] 특정 항목 수동 분리 (Rating Key 입력 시 무조건 먼저 실행)
    # -------------------------------------------------------------------------
    if target_rk.isdigit():
        task.log(f"-> [수동 분리 요청] ID {target_rk} 항목 분리를 먼저 시도합니다...")
        try:
            plex_instance = core_api['get_plex']()
            item = plex_instance.fetchItem(int(target_rk))
            title = item.title
            try:
                item.split()
                task.log(f"-> 🟢 [수동 분리 완료] '{title}' 항목이 분리되었습니다! (이제 목록을 새로고침 합니다.)")
            except Exception as e:
                task.log(f"-> 🟡 [수동 분리 스킵] '{title}' (이미 분리되었거나 분리할 수 없는 항목: {e})")
        except Exception as e:
            task.log(f"-> 🔴 [수동 분리 오류] ID {target_rk} 항목을 서버에서 찾을 수 없습니다: {e}")

    # (수동 분리를 마친 후, 화면을 유지하기 위해 사용자가 선택한 검색을 연달아 실행합니다)

    # -------------------------------------------------------------------------
    # [작업 1 & 2] 다중 경로 검색 및 일괄 분리(Split)
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
                
                key_encoded = urllib.parse.quote(f"/library/metadata/{rk_id}", safe='')
                if machine_id:
                    custom_plex_url = f"https://plex.padossi.com/web/index.html#!/server/{machine_id}/details?key={key_encoded}"
                    official_plex_url = f"https://app.plex.tv/desktop/#!/server/{machine_id}/details?key={key_encoded}"
                else:
                    custom_plex_url = "#"
                    official_plex_url = "#"
                    
                html_title = f"<div style='margin-bottom: 4px;'><a href='{custom_plex_url}' target='_blank' style='color: #007bff; text-decoration: underline; font-weight: bold; cursor: pointer;' title='개인 도메인으로 열기'>{title}</a></div>"
                html_title += f"<div><a href='{official_plex_url}' target='_blank' style='color: #6c757d; font-size: 0.85em; text-decoration: none; padding: 2px 6px; border: 1px solid #ccc; border-radius: 4px;' title='Plex 공식 웹으로 열기'>공식 앱 열기 ↗</a></div>"
                
                root_paths = set()
                
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

                if len(root_paths) > 1:
                    display_folders = [os.path.basename(p) for p in root_paths]
                    folder_html = "".join([f"<div style='margin-bottom: 4px; font-size: 0.9em; white-space: nowrap; background-color: rgba(255,255,255,0.05); padding: 4px; border-radius: 4px;'>📂 {f}</div>" for f in display_folders])
                    
                    item_data = {
                        "section": sec_name,
                        "title": html_title,       
                        "raw_title": title,        
                        "rating_key": str(rk_id),  
                        "count": f"<span style='color:#e5a00d; font-weight:bold;'>{len(root_paths)}</span>",
                        "raw_count": len(root_paths),
                        "folders": folder_html     
                    }
                    results.append(item_data)
                    targets_to_split.append(item_data)
            
            if work_type == 'split_multipath' and targets_to_split:
                task.log(f"3. 총 {len(targets_to_split)}개의 병합 의심 항목을 분리(Split) 합니다...")
                try:
                    plex_instance = core_api['get_plex']()
                    split_count = 0
                    for split_idx, target in enumerate(targets_to_split, 1):
                        task.log(f"   -> [{split_idx}/{len(targets_to_split)}] 분리 시도: {target['raw_title']}")
                        try:
                            item = plex_instance.fetchItem(int(target['rating_key']))
                            item.split()
                            split_count += 1
                            target['count'] = "<span style='color:#28a745; font-weight:bold;'>분리 완료</span>"
                        except Exception as e:
                            task.log(f"   -> 분리 실패 ({target['raw_title']}): {e}")
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
                    {"key": "section", "label": "섹션", "width": "10%", "align": "left", "header_align": "center", "sortable": True},
                    {"key": "title", "label": "제목 (클릭 시 새창 열림)", "width": "35%", "align": "left", "header_align": "center", "sortable": True, "sort_key": "raw_title", "sort_type": "string"},
                    {"key": "folders", "label": "감지된 폴더명", "width": "35%", "align": "left", "header_align": "center", "sortable": False},
                    {"key": "rating_key", "label": "ID (RK)", "width": "10%", "align": "center", "header_align": "center", "sortable": False},
                    {"key": "count", "label": "상태/수", "width": "10%", "align": "center", "header_align": "center", "sortable": True, "sort_key": "raw_count", "sort_type": "number"}
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
        task.log("1. 분석 대상 컨텐츠 목록 수집 중...")
        
        query = """
            SELECT mi.id, mi.title, mi.guid, mi.metadata_type, ls.name AS section_name
            FROM metadata_items mi
            JOIN library_sections ls ON mi.library_section_id = ls.id
            WHERE (? = 'all' OR ls.id = ?) AND mi.metadata_type IN (1, 2)
        """
        
        try:
            candidates = core_api['query'](query, (section_id, section_id))
            
            task.log("2. 동일한 GUID를 가진 중복 항목을 대조합니다...")
            
            guid_map = {}
            for item in candidates:
                g = item['guid']
                if not g: continue
                clean_guid = g.split("://")[-1].split("?")[0] if "://" in g else g
                if clean_guid not in guid_map:
                    guid_map[clean_guid] = []
                guid_map[clean_guid].append(item)
            
            results = []
            for clean_guid, items in guid_map.items():
                if len(items) > 1:
                    for item in items:
                        rk_id = item['id']
                        title = item['title']
                        m_type = item['metadata_type']
                        sec_name = item['section_name']
                        
                        key_encoded = urllib.parse.quote(f"/library/metadata/{rk_id}", safe='')
                        if machine_id:
                            custom_plex_url = f"https://plex.padossi.com/web/index.html#!/server/{machine_id}/details?key={key_encoded}"
                            official_plex_url = f"https://app.plex.tv/desktop/#!/server/{machine_id}/details?key={key_encoded}"
                        else:
                            custom_plex_url = "#"
                            official_plex_url = "#"
                            
                        html_title = f"<div style='margin-bottom: 4px;'><a href='{custom_plex_url}' target='_blank' style='color: #007bff; text-decoration: underline; font-weight: bold; cursor: pointer;' title='개인 도메인으로 열기'>{title}</a></div>"
                        html_title += f"<div><a href='{official_plex_url}' target='_blank' style='color: #6c757d; font-size: 0.85em; text-decoration: none; padding: 2px 6px; border: 1px solid #ccc; border-radius: 4px;' title='Plex 공식 웹으로 열기'>공식 앱 열기 ↗</a></div>"
                        
                        root_paths = set()
                        if m_type == 1:
                            files = core_api['query']("SELECT mp.file FROM media_items m JOIN media_parts mp ON mp.media_item_id = m.id WHERE m.metadata_item_id = ?", (rk_id,))
                            for row in files:
                                if row.get('file'):
                                    root_paths.add(extract_movie_folder(unicodedata.normalize('NFC', row['file'])))
                        elif m_type == 2:
                            files = core_api['query']("SELECT mp.file FROM metadata_items ep JOIN metadata_items sea ON ep.parent_id = sea.id JOIN media_items m ON m.metadata_item_id = ep.id JOIN media_parts mp ON mp.media_item_id = m.id WHERE sea.parent_id = ? AND ep.metadata_type = 4", (rk_id,))
                            for row in files:
                                if row.get('file'):
                                    root_paths.add(get_unique_root_path(unicodedata.normalize('NFC', row['file'])))
                        
                        display_folders = [os.path.basename(p) for p in root_paths]
                        folder_html = "".join([f"<div style='margin-bottom: 4px; font-size: 0.9em; white-space: nowrap; background-color: rgba(255,255,255,0.05); padding: 4px; border-radius: 4px;'>📂 {f}</div>" for f in display_folders])

                        results.append({
                            "section": sec_name,
                            "title": html_title,
                            "raw_title": title,
                            "rating_key": str(rk_id),
                            "guid": clean_guid,
                            "folders": folder_html
                        })

            task.log(f"검색 완료! {len(results):,}건의 중복(동일 GUID) 항목을 찾았습니다.")
            task.update_state('running', progress=100, total=100)

            return {
                "status": "success",
                "type": "datatable",
                "default_sort": [{"key": "guid", "dir": "asc"}, {"key": "title", "dir": "asc"}],
                "columns": [
                    {"key": "section", "label": "섹션", "width": "10%", "align": "left", "header_align": "center", "sortable": True},
                    {"key": "title", "label": "제목 (클릭 시 새창 열림)", "width": "25%", "align": "left", "header_align": "center", "sortable": True, "sort_key": "raw_title", "sort_type": "string"},
                    {"key": "folders", "label": "감지된 폴더명", "width": "30%", "align": "left", "header_align": "center", "sortable": False},
                    {"key": "rating_key", "label": "ID (RK)", "width": "10%", "align": "center", "header_align": "center", "sortable": False},
                    {"key": "guid", "label": "Plex GUID", "width": "25%", "align": "left", "header_align": "center", "sortable": True}
                ],
                "data": results
            }, 200

        except Exception as e:
            task.log(f"GUID 검색 중 오류: {str(e)}")
            return {"status": "error", "message": f"GUID 검색 중 오류: {str(e)}"}, 500
