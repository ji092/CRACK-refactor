def normalize_region_name(address: str) -> str | None:
    if not address:
        return None

    text = str(address).strip()

    region_alias_map = {
        "서울": "서울특별시",
        "서울시": "서울특별시",
        "서울특별시": "서울특별시",

        "부산": "부산광역시",
        "부산시": "부산광역시",
        "부산광역시": "부산광역시",

        "대구": "대구광역시",
        "대구시": "대구광역시",
        "대구광역시": "대구광역시",

        "인천": "인천광역시",
        "인천시": "인천광역시",
        "인천광역시": "인천광역시",

        "광주": "광주광역시",
        "광주시": "광주광역시",
        "광주광역시": "광주광역시",

        "대전": "대전광역시",
        "대전시": "대전광역시",
        "대전광역시": "대전광역시",

        "울산": "울산광역시",
        "울산시": "울산광역시",
        "울산광역시": "울산광역시",

        "세종": "세종특별자치시",
        "세종시": "세종특별자치시",
        "세종특별자치시": "세종특별자치시",

        "경기": "경기도",
        "경기도": "경기도",

        "강원": "강원특별자치도",
        "강원도": "강원특별자치도",
        "강원특별자치도": "강원특별자치도",

        "충북": "충청북도",
        "충청북도": "충청북도",

        "충남": "충청남도",
        "충청남도": "충청남도",

        "전북": "전북특별자치도",
        "전라북도": "전북특별자치도",
        "전북특별자치도": "전북특별자치도",

        "전남": "전라남도",
        "전라남도": "전라남도",

        "경북": "경상북도",
        "경상북도": "경상북도",

        "경남": "경상남도",
        "경상남도": "경상남도",

        "제주": "제주특별자치도",
        "제주도": "제주특별자치도",
        "제주특별자치도": "제주특별자치도",
    }

    # 1) 공백 기준 첫 토큰 우선 확인
    first_token = text.split()[0] if text.split() else text
    if first_token in region_alias_map:
        return region_alias_map[first_token]

    # 2) 주소 앞부분이 지역명으로 시작하는지 확인
    for alias, normalized in region_alias_map.items():
        if text.startswith(alias):
            return normalized

    # 3) 도/시가 빠진 주소 일부 예외 처리
    city_to_province_map = {
        "수원": "경기도",
        "성남": "경기도",
        "용인": "경기도",
        "고양": "경기도",
        "안양": "경기도",
        "부천": "경기도",
        "화성": "경기도",
        "춘천": "강원특별자치도",
        "원주": "강원특별자치도",
        "강릉": "강원특별자치도",
        "청주": "충청북도",
        "천안": "충청남도",
        "전주": "전북특별자치도",
        "목포": "전라남도",
        "포항": "경상북도",
        "창원": "경상남도",
        "제주": "제주특별자치도",
    }

    for city, province in city_to_province_map.items():
        if text.startswith(city):
            return province

    return None

def parse_region_hierarchy(address: str) -> list[str]:
    if not address:
        return []

    text = str(address).strip().replace(",", " ")
    if text.startswith("위치 정보 없음") or text in ("-", "", "주소 정보 없음"):
        return []
    parts = [p for p in text.split() if p]

    if not parts:
        return []

    # 첫 토큰이 좌표/숫자면 지역명이 아니므로 제거
    while parts and parts[0].replace('.', '', 1).replace('-', '', 1).isdigit():
        parts.pop(0)

    if not parts:
        return []

    region_alias_map = {
        "서울": "서울특별시",
        "서울시": "서울특별시",
        "서울특별시": "서울특별시",

        "부산": "부산광역시",
        "부산시": "부산광역시",
        "부산광역시": "부산광역시",

        "대구": "대구광역시",
        "대구시": "대구광역시",
        "대구광역시": "대구광역시",

        "인천": "인천광역시",
        "인천시": "인천광역시",
        "인천광역시": "인천광역시",

        "광주": "광주광역시",
        "광주시": "광주광역시",
        "광주광역시": "광주광역시",

        "대전": "대전광역시",
        "대전시": "대전광역시",
        "대전광역시": "대전광역시",

        "울산": "울산광역시",
        "울산시": "울산광역시",
        "울산광역시": "울산광역시",

        "세종": "세종특별자치시",
        "세종시": "세종특별자치시",
        "세종특별자치시": "세종특별자치시",

        "경기": "경기도",
        "경기도": "경기도",

        "강원": "강원특별자치도",
        "강원도": "강원특별자치도",
        "강원특별자치도": "강원특별자치도",

        "충북": "충청북도",
        "충청북도": "충청북도",

        "충남": "충청남도",
        "충청남도": "충청남도",

        "전북": "전북특별자치도",
        "전라북도": "전북특별자치도",
        "전북특별자치도": "전북특별자치도",

        "전남": "전라남도",
        "전라남도": "전라남도",

        "경북": "경상북도",
        "경상북도": "경상북도",

        "경남": "경상남도",
        "경상남도": "경상남도",

        "제주": "제주특별자치도",
        "제주도": "제주특별자치도",
        "제주특별자치도": "제주특별자치도",
    }

    allowed_top_regions = {
        "서울특별시",
        "부산광역시",
        "대구광역시",
        "인천광역시",
        "광주광역시",
        "대전광역시",
        "울산광역시",
        "세종특별자치시",
        "경기도",
        "강원특별자치도",
        "충청북도",
        "충청남도",
        "전북특별자치도",
        "전라남도",
        "경상북도",
        "경상남도",
        "제주특별자치도",
    }

    city_to_province_map = {
        "수원": "경기도",
        "성남": "경기도",
        "용인": "경기도",
        "고양": "경기도",
        "안양": "경기도",
        "부천": "경기도",
        "화성": "경기도",
        "춘천": "강원특별자치도",
        "원주": "강원특별자치도",
        "강릉": "강원특별자치도",
        "청주": "충청북도",
        "천안": "충청남도",
        "전주": "전북특별자치도",
        "목포": "전라남도",
        "포항": "경상북도",
        "창원": "경상남도",
        "제주": "제주특별자치도",
    }

    inferred_city_token = None
    matched_city_prefix = None
    result = []

    # 1단계: 광역/도
    first = parts[0]
    top_region = region_alias_map.get(first)

    # 별칭 맵에 없는 경우, 일부 주요 시 이름은 도 단위로 보정
    # 별칭 맵에 없는 경우, 일부 주요 시 이름은 도 단위로 보정
    if not top_region:
        for city, province in city_to_province_map.items():
            if first.startswith(city):
                top_region = province
                matched_city_prefix = city

                if first.endswith("시"):
                    inferred_city_token = first
                else:
                    inferred_city_token = f"{city}시"

                break

    # 전국 17개 시도에 없는 값은 전부 기타 처리
    if not top_region or top_region not in allowed_top_regions:
        return ["기타"]

    result.append(top_region)

    # 첫 토큰이 "수원시", "성남시"처럼 도시였던 경우
    # top_region만 넣으면 ["경기도"]가 되어 버리므로 city도 같이 넣어
    # 항상 ["경기도", "수원시"] 형태를 유지한다.
    if inferred_city_token and top_region == "경기도":
        result.append(inferred_city_token)

    # 2단계: 시/군/구
    # 2단계: 하위 지역 파싱
    for token in parts[1:]:
        top_region = result[0]

        # 1) 경기도: 시 -> 구/군
        if top_region == "경기도":
            if len(result) == 1:
                if token.endswith("시") and token not in result:
                    result.append(token)
            elif len(result) == 2:
                if token.endswith(("구", "군")) and token not in result:
                    result.append(token)

        # 2) 서울/광역시/세종: 구/군까지만
        elif top_region.endswith("시"):
            if len(result) == 1:
                if token.endswith(("구", "군")) and token not in result:
                    result.append(token)

        # 3) 그 외 도 지역: 시/군 -> 읍/면/동
        else:
            if len(result) == 1:
                if token.endswith(("시", "군")) and token not in result:
                    result.append(token)
            elif len(result) == 2:
                if token.endswith(("읍", "면", "동")) and token not in result:
                    result.append(token)

        if len(result) >= 3:
            break

    return result