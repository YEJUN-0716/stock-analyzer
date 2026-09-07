"""선수 평점 — FotMob 비공식 JSON(`/api/data/`) 한 소스.

왜 여기뿐인가 (2026-09-06 실측):
  API-Football  평점을 주지만 무료 플랜은 2022~2024 시즌만 (이번 시즌은 Pro $19/월)
  SofaScore     403
  FotMob        200 + 이번 시즌 경기별 평점이 실제로 온다  ← 유일

**공식 API 가 아니다.** 키는 필요 없지만 언제든 막힐 수 있다. 그래서 이 모듈은
실패를 위로 던지지 않고 빈 값을 돌려준다 — 평점이 죽어도 순위·일정
(openfootball)은 살아 있어야 한다.
"""
from __future__ import annotations

import re
from datetime import datetime

from epl import CACHE_DIR, LONDON, cached_json

TEAM_ID = 8455   # Chelsea
EPL_ID = 47      # FotMob 의 프리미어리그 id (openfootball 과 다른 체계다)

TEAM_URL = "https://www.fotmob.com/api/data/teams?id={team_id}"
MATCH_URL = "https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

# 순위표·부상·진행중 판단이 전부 이 응답에서 나온다. 6시간이면 라이브가 안 보인다.
TEAM_TTL = 300
# 끝난 경기의 평점은 더 안 변한다. 한 경기가 260KB 라 다시 받을 이유가 없다.
MATCH_TTL = 10 ** 9
# 진행중인 경기는 반대다 — 평점도 스코어도 계속 바뀐다.
LIVE_TTL = 60

RATING = "FotMob rating"
MINUTES = "Minutes played"


def _stat(player: dict, key: str):
    """playerStats 한 명에서 숫자 하나. 없으면 None(벤치는 stats 가 빈 리스트다)."""
    for block in player.get("stats") or []:
        cell = (block.get("stats") or {}).get(key)
        if isinstance(cell, dict):
            return (cell.get("stat") or {}).get("value")
    return None


def team(team_id: int = TEAM_ID, ttl: int = TEAM_TTL) -> dict:
    """팀 응답 — 순위표·일정·스쿼드·부상이 다 여기 있다(캐시 파일 하나)."""
    return cached_json(
        TEAM_URL.format(team_id=team_id), CACHE_DIR / f"fotmob-team-{team_id}.json", ttl
    )


def fixtures(team_id: int = TEAM_ID, league_id: int | None = EPL_ID,
             ongoing: bool = False) -> list[dict]:
    """끝난 경기(오래된 것부터). league_id 를 주면 그 대회만 — 컵 경기가 섞이지 않게.

    ongoing=True 면 **뛰고 있는 경기의 잠정 스코어도** 넣는다. 기본은 뺀다 —
    폼 배지나 최근 결과에 아직 안 끝난 경기가 승패로 찍히면 안 된다.
    """
    out = []
    for f in (team(team_id).get("fixtures", {}).get("allFixtures", {}) or {}).get("fixtures", []):
        status = f.get("status") or {}
        if not (status.get("finished") or (ongoing and status.get("ongoing"))):
            continue
        if league_id and (f.get("tournament") or {}).get("leagueId") != league_id:
            continue
        home = (f.get("home") or {}).get("id") == team_id
        out.append(dict(
            id=f["id"],
            comp=(f.get("tournament") or {}).get("name", ""),
            date=(status.get("utcTime") or "")[:10],
            opp=(f.get("opponent") or {}).get("name", ""),
            opp_id=(f.get("opponent") or {}).get("id"),
            home=home,
            # 스코어는 홈-원정 순서로 온다. 'scoreStr' 을 우리 팀 기준으로 읽으면
            # 원정 경기가 통째로 뒤집힌다 — home/away 의 score 를 그대로 쓴다.
            home_goals=(f.get("home") or {}).get("score"),
            away_goals=(f.get("away") or {}).get("score"),
            score=status.get("scoreStr", ""),
        ))
    return sorted(out, key=lambda m: m["date"])


def table(team_id: int = TEAM_ID) -> list[dict]:
    """리그 순위표 — `epl.table()` 과 **같은 행 모양**으로 돌려준다.

    소스를 갈아탄 이유: openfootball 은 주 1회(수요일)만 결과를 올린다.
    9/2 다음 커밋이 없어서 3라운드가 통째로 비어 있었다 — 캐시가 아니라 소스다.
    모양을 맞춰 두면 화면(view)과 나머지 계산은 하나도 안 건드린다.
    """
    blocks = team(team_id).get("table") or []
    rows = ((blocks[0].get("data") if blocks else {}) or {}).get("table", {}).get("all", [])
    out = []
    for r in rows:
        gf, ga = (int(x) for x in r["scoresStr"].split("-"))
        out.append(dict(team=r["name"], rank=r["idx"], p=r["played"], w=r["wins"],
                        d=r["draws"], l=r["losses"], gf=gf, ga=ga,
                        gd=r["goalConDiff"], pts=r["pts"]))
    return out


def names(team_id: int = TEAM_ID) -> dict[int, str]:
    """FotMob 팀 id → 순위표 이름. 컵 상대(2부 팀 등)는 순위표에 없어서 빠진다."""
    blocks = team(team_id).get("table") or []
    rows = ((blocks[0].get("data") if blocks else {}) or {}).get("table", {}).get("all", [])
    return {r["id"]: r["name"] for r in rows}


LOGO_URL = "https://images.fotmob.com/image_resources/logo/teamlogo/{team_id:d}_small.png"


def ids_by_name(team_id: int = TEAM_ID) -> dict[str, int]:
    """팀 이름 → FotMob 팀 id. 순위표(리그 20팀)와 일정의 상대(컵 팀)를 함께 훑는다.

    이름이 열쇠인 이유는 화면·계산이 전부 이름으로 돌기 때문이다
    (두 소스의 이름이 epl.short() 뒤에 정확히 일치한다).
    """
    out = {name: tid for tid, name in names(team_id).items()}
    for f in (team(team_id).get("fixtures", {}).get("allFixtures", {}) or {}).get("fixtures", []):
        opp = f.get("opponent") or {}
        if opp.get("id"):
            out.setdefault(opp.get("name", ""), int(opp["id"]))
    out.pop("", None)
    return out


def logos(team_id: int = TEAM_ID) -> dict[str, str]:
    """팀 이름 → 엠블럼 주소.

    주소는 팀 id(정수)로만 만든다 — 외부 문자열이 주소에 섞이지 않는다.
    """
    return {name: LOGO_URL.format(team_id=tid) for name, tid in ids_by_name(team_id).items()}


def competitions(team_id: int = TEAM_ID) -> list[dict]:
    """이번 시즌 이 팀이 뛰는 대회들 — 화면의 선택지가 된다."""
    seen: dict[int, dict] = {}
    for f in (team(team_id).get("fixtures", {}).get("allFixtures", {}) or {}).get("fixtures", []):
        t = f.get("tournament") or {}
        if t.get("leagueId"):
            c = seen.setdefault(t["leagueId"], dict(id=t["leagueId"], name=t.get("name", ""), n=0))
            c["n"] += 1
    return sorted(seen.values(), key=lambda c: -c["n"])


def schedule(team_id: int = TEAM_ID, league_id: int | None = None) -> list[dict]:
    """아직 안 치른 경기 — **openfootball 경기 모양**(런던 시각).

    일정도 FotMob 에서 받는 이유는 컵 때문이다. openfootball 은 리그만 준다.
    """
    ids = names(team_id)
    me = ids.get(team_id, "")
    out = []
    for f in (team(team_id).get("fixtures", {}).get("allFixtures", {}) or {}).get("fixtures", []):
        st = f.get("status") or {}
        if st.get("finished") or st.get("ongoing") or not st.get("utcTime"):
            continue
        if league_id and (f.get("tournament") or {}).get("leagueId") != league_id:
            continue
        when = datetime.fromisoformat(st["utcTime"].replace("Z", "+00:00")).astimezone(LONDON)
        home = (f.get("home") or {}).get("id") == team_id
        opp = ids.get((f.get("opponent") or {}).get("id")) or (f.get("opponent") or {}).get("name", "")
        out.append(dict(
            id=f["id"],
            date=when.strftime("%Y-%m-%d"), time=when.strftime("%H:%M"),
            round=(f.get("tournament") or {}).get("name", ""),
            team1=me if home else opp, team2=opp if home else me, score=None,
        ))
    return sorted(out, key=lambda m: (m["date"], m["time"]))


def as_matches(team_id: int = TEAM_ID, league_id: int | None = EPL_ID,
               ongoing: bool = False) -> list[dict]:
    """끝난 경기 → **openfootball 경기 모양**. epl.py 의 계산을 그대로 쓰려고.

    팀 이름은 순위표 쪽 이름을 쓴다 — 일정에 있는 'Brighton' 이 아니라
    'Brighton and Hove Albion'. openfootball 이름을 epl.short() 로 줄인 것과
    20팀 전부 일치한다(그래서 매핑 표가 필요 없다).
    """
    ids = names(team_id)
    me = ids.get(team_id, "")
    out = []
    for f in fixtures(team_id, league_id, ongoing):
        opp = ids.get(f["opp_id"], f["opp"])
        out.append(dict(
            date=f["date"], time="", round=f["comp"],
            team1=me if f["home"] else opp,
            team2=opp if f["home"] else me,
            score={"ft": [int(f["home_goals"]), int(f["away_goals"])]},
        ))
    assert not ids or me, "순위표에서 우리 팀을 못 찾았다 — id 체계가 바뀌었다"
    return out


def details(match_id: int, ttl: int = MATCH_TTL) -> dict:
    """경기 상세(평점·라인업). 진행중이면 ttl 을 짧게 줘서 다시 받는다."""
    return cached_json(
        MATCH_URL.format(match_id=match_id), CACHE_DIR / f"fotmob-match-{match_id}.json", ttl
    )


def live_match(team_id: int = TEAM_ID) -> dict | None:
    """지금 뛰고 있는 경기. 없으면 None.

    끝난 경기 목록(fixtures)에는 안 잡힌다 — 저긴 finished 만 본다.
    """
    fx = (team(team_id, LIVE_TTL).get("fixtures", {}).get("allFixtures", {}) or {})
    for f in fx.get("fixtures", []):
        st = f.get("status") or {}
        if not st.get("ongoing"):
            continue
        # 시계와 스코어는 **경기 상세**에서 읽는다. 팀 응답의 liveTime 은 낡는다 —
        # 후반 8분에도 '1분'이라고 했고, 상세는 같은 순간에 48분이었다.
        detail = {}
        try:
            detail = (details(f["id"], LIVE_TTL).get("header") or {}).get("status") or {}
        except Exception:
            pass
        live = detail.get("liveTime") or {}
        clock = live.get("short") or (detail.get("reason") or {}).get("long", "")
        return dict(id=f["id"], opp=(f.get("opponent") or {}).get("name", ""),
                    home=(f.get("home") or {}).get("id") == team_id,
                    score=detail.get("scoreStr") or st.get("scoreStr", ""),
                    # 방향 표시 문자(U+200E)가 섞여 온다 — 화면엔 안 보이지만 지운다
                    clock=clock.replace("‎", "").strip())
    return None


def lineup(match_id: int, team_id: int = TEAM_ID, ttl: int = LIVE_TTL) -> dict:
    """선발·교체와 각자의 현재 평점. 포메이션과 팀 평균 평점도 같이 온다.

    교체는 **뛴 선수만** 넣는다 — 벤치에 앉아만 있으면 평점이 없다.
    """
    lu = (details(match_id, ttl).get("content", {}) or {}).get("lineup") or {}
    side = next((lu.get(k) for k in ("homeTeam", "awayTeam")
                 if (lu.get(k) or {}).get("id") == team_id), None)
    if not side:
        return {}

    def rows(players):
        out = []
        for pl in players or []:
            perf = pl.get("performance") or {}
            if perf.get("rating") is None:
                continue
            out.append(dict(id=pl.get("id"), name=pl.get("name", ""),
                            shirt=str(pl.get("shirtNumber") or ""),
                            rating=float(perf["rating"]),
                            season=perf.get("seasonRating")))
        return out

    # 경기 전에도 라인업이 오는데 그건 예상이 아니라 '지난 경기 선발'이다
    # (lineupType='lastStarting11'). 이름표를 그대로 실어 보내 화면이 거짓말을 안 하게 한다.
    return dict(formation=side.get("formation", ""), rating=side.get("rating"),
                kind=lu.get("lineupType", ""),
                starters=rows(side.get("starters")), subs=rows(side.get("subs")))


# 화면에 쓰는 이름. 모르는 항목은 FotMob 이 준 영어 제목을 그대로 쓴다.
STAT_LABELS = {
    "BallPossesion": "점유율 %", "expected_goals": "기대 득점 xG",
    "total_shots": "슈팅", "ShotsOnTarget": "유효 슈팅",
    "ShotsOffTarget": "빗나간 슈팅", "blocked_shots": "막힌 슈팅",
    "touches_opp_box": "상대 박스 터치", "big_chance": "빅 찬스",
    "big_chance_missed_title": "빅 찬스 실패", "accurate_passes": "정확한 패스",
    "corners": "코너킥", "fouls": "파울", "yellow_cards": "경고",
    "keeper_saves": "선방", "duel_won": "경합 승리",
    "expected_goals_on_target": "유효슈팅 기대값 xGOT",
    "shots_woodwork": "골대 맞음", "shots_inside_box": "박스 안 슈팅",
    "shots_outside_box": "박스 밖 슈팅", "expected_goals_open_play": "오픈 플레이 xG",
    "expected_goals_set_play": "세트피스 xG", "expected_goals_non_penalty": "PK 제외 xG",
    "physical_metrics_distance_covered": "뛴 거리 (m)",
    "physical_metrics_sprinting": "스프린트 거리 (m)",
    "physical_metrics_number_of_sprints": "스프린트 횟수",
    "passes": "패스", "own_half_passes": "자기 진영 패스",
    "opposition_half_passes": "상대 진영 패스", "long_balls_accurate": "정확한 롱볼",
    "accurate_crosses": "정확한 크로스", "player_throws": "스로인", "Offsides": "오프사이드",
    "matchstats.headers.tackles": "태클", "interceptions": "인터셉트",
    "shot_blocks": "슈팅 차단", "clearances": "걷어내기",
    "ground_duels_won": "지상 경합 승리", "aerials_won": "공중 경합 승리",
    "dribbles_succeeded": "드리블 성공", "red_cards": "퇴장",
}


def _number(value):
    """'372 (84%)' 이나 '1.98' 에서 막대에 쓸 숫자만. 못 읽으면 None."""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"[-+]?\d*\.?\d+", str(value).strip()) if value is not None else None
    return float(m.group()) if m else None


# 스탯 묶음 이름. FotMob 이 주는 그룹 키 그대로다.
GROUP_LABELS = {
    "top_stats": "요약", "shots": "슈팅", "expected_goals": "기대 득점",
    "physical_metrics": "활동량", "passes": "패스", "defence": "수비",
    "duels": "경합", "discipline": "규율",
}


def stat_groups(match_id: int, ttl: int = MATCH_TTL) -> list[dict]:
    """이 경기에 있는 스탯 묶음들 — 화면의 탭이 된다. 없는 묶음은 안 만든다."""
    periods = ((details(match_id, ttl).get("content", {}).get("stats") or {}).get("Periods") or {})
    out = []
    for g in (periods.get("All") or {}).get("stats") or []:
        key = g.get("key", "")
        if any(r.get("stats") and r["stats"][0] is not None for r in g.get("stats") or []):
            out.append(dict(key=key, title=GROUP_LABELS.get(key, g.get("title", key))))
    return out


def match_stats(match_id: int, group: str = "top_stats", ttl: int = MATCH_TTL) -> dict:
    """한 경기의 팀 스탯 — 기본은 'Top stats'(점유율·xG·슈팅·빅찬스…).

    **xG 가 여기 있다.** 1단계에선 Understat 이 봇에게 xG 를 안 줘서 뺐던 값이다.
    값은 [홈, 원정] 순서로 온다 — 우리 팀 기준이 아니다.
    """
    d = details(match_id, ttl)
    teams = [t.get("name", "") for t in ((d.get("header") or {}).get("teams") or [])]
    periods = ((d.get("content", {}).get("stats") or {}).get("Periods") or {})
    groups = (periods.get("All") or {}).get("stats") or []
    block = next((g for g in groups if g.get("key") == group), None)
    rows = []
    for r in (block or {}).get("stats") or []:
        pair = r.get("stats") or [None, None]
        if pair[0] is None or pair[1] is None:
            continue                      # 그룹 제목 줄은 값이 비어 있다
        rows.append(dict(
            title=STAT_LABELS.get(r.get("key"), r.get("title", "")),
            home=str(pair[0]), away=str(pair[1]),
            home_n=_number(pair[0]), away_n=_number(pair[1]),
        ))
    return dict(teams=teams, rows=rows)


def season_stats(team_id: int = TEAM_ID, league_id: int | None = EPL_ID,
                 group: str = "top_stats") -> list[dict]:
    """이번 시즌 **팀 평균** 스탯 — 그 팀 시점으로(낸 값, 내준 값).

    경기 스탯은 [홈, 원정] 순서라 우리가 어느 쪽이었는지 보고 골라야 한다.
    한 경기라도 값이 없으면 그 항목은 그 경기를 안 센다(있는 것만 평균).
    """
    got: dict[str, list[list[float]]] = {}
    order: list[str] = []
    me = names(team_id).get(team_id, "")
    for f in fixtures(team_id, league_id):
        stats = match_stats(f["id"], group=group)
        side = stats["teams"][0 if f["home"] else 1] if len(stats["teams"]) == 2 else ""
        # 스탯은 [홈, 원정] 순서다. 우리가 아는 홈 여부와 어긋나면 평균이 조용히
        # 뒤집히므로 여기서 멈춘다(이름은 두 소스가 같은 체계다).
        assert not me or not side or side == me, f"{f['date']}: 스탯 순서가 어긋난다({side})"
        for r in stats["rows"]:
            ours, theirs = (r["home_n"], r["away_n"]) if f["home"] else (r["away_n"], r["home_n"])
            if ours is None or theirs is None:
                continue
            if r["title"] not in got:
                got[r["title"]] = [[], []]
                order.append(r["title"])
            got[r["title"]][0].append(ours)
            got[r["title"]][1].append(theirs)
    return [
        dict(title=t, ours=sum(got[t][0]) / len(got[t][0]),
             theirs=sum(got[t][1]) / len(got[t][1]), n=len(got[t][0]))
        for t in order if got[t][0]
    ]


def insights(match_id: int, ttl: int = TEAM_TTL) -> list[dict]:
    """FotMob 이 그 경기에 붙여 둔 한 줄 사실들('5경기 무패' 같은).

    예측이 아니다 — 지금까지의 기록을 문장으로 만든 것이다. 그래서 그대로 옮긴다.
    """
    mf = (details(match_id, ttl).get("content") or {}).get("matchFacts") or {}
    out = []
    for i in mf.get("insights") or []:
        text = i.get("text") or i.get("defaultText") or ""
        if text:
            out.append(dict(team_id=i.get("teamId"), text=text))
    return out


def match_ratings(match_id: int, team_id: int = TEAM_ID) -> list[dict]:
    """한 경기에서 그 팀 선수들의 평점(높은 순). 평점이 없는 선수(미출전)는 뺀다."""
    data = details(match_id)
    out = []
    for p in (data.get("content", {}).get("playerStats") or {}).values():
        if p.get("teamId") != team_id:
            continue
        rating = _stat(p, RATING)
        if rating is None:
            continue
        out.append(dict(
            id=p.get("id"), name=p.get("name", ""), rating=float(rating),
            minutes=_stat(p, MINUTES) or 0,
        ))
    return sorted(out, key=lambda r: -r["rating"])


def season_rows(team_id: int = TEAM_ID, league_id: int | None = EPL_ID) -> list[dict]:
    """(경기 × 선수) 한 줄씩. 경기 하나가 실패해도 나머지는 살린다."""
    rows = []
    for m in fixtures(team_id, league_id):
        try:
            players = match_ratings(m["id"], team_id)
        except Exception:
            continue
        for p in players:
            rows.append({**p, "match": m["id"], "date": m["date"], "opp": m["opp"]})
    return rows


def average(rows: list[dict], min_matches: int = 1) -> list[dict]:
    """선수별 평균 평점(높은 순). 순수 함수 — 네트워크를 안 탄다.

    경기 평점의 **단순 평균**이다. 출전 시간으로 가중하지 않는다:
    5분 뛴 경기와 90분 뛴 경기가 같은 무게라는 뜻이고, `min_matches` 로
    표본이 얇은 선수를 잘라내는 게 그 대가다. FotMob 자신의 시즌 평점과
    얼마나 갈리는지는 _selfcheck() 가 잰다.
    """
    by: dict[int, dict] = {}
    for r in rows:
        p = by.setdefault(r["id"], dict(id=r["id"], name=r["name"], n=0, minutes=0, total=0.0))
        p["n"] += 1
        p["minutes"] += r["minutes"]
        p["total"] += r["rating"]
        p["name"] = r["name"]
    out = [dict(p, avg=p["total"] / p["n"]) for p in by.values() if p["n"] >= min_matches]
    return sorted(out, key=lambda p: -p["avg"])


def injuries(team_id: int = TEAM_ID) -> list[dict]:
    """지금 못 뛰는 선수 — 이름과 복귀 예상.

    같은 응답 안에 명단이 두 군데 있고 **서로 다르다**(2026-09-07 실측):
      squad[].members[].injury                    ← 여기를 쓴다. 지금 상태다.
      overview.lastLineupStats.unavailable        지난 경기 시점의 결장자다
    이름이 붙은 그릇('lastLineupStats')이 답을 갖고 있다 — 지난 경기에 못 뛴
    선수(Enzo)와 지금 의심스러운 선수(Caicedo)는 다른 명단이다. 프리뷰는 다음
    경기를 묻는 화면이므로 앞쪽이다.

    부상 '종류'는 못 준다 — `injuryId` 는 숫자 코드고 라벨이 응답에 없다.
    """
    out = []
    for group in (team(team_id).get("squad", {}) or {}).get("squad", []):
        for m in group.get("members", []):
            hurt = m.get("injury")
            if hurt:
                out.append(dict(id=m.get("id"), name=m.get("name", ""),
                                expected=hurt.get("expectedReturn", "")))
    return out


def squad_ratings(team_id: int = TEAM_ID) -> dict[int, float]:
    """FotMob 이 스스로 매긴 시즌 평점 — 우리 평균을 대조할 다른 경로."""
    out = {}
    for group in (team(team_id).get("squad", {}) or {}).get("squad", []):
        for m in group.get("members", []):
            if m.get("rating") is not None:
                out[m["id"]] = float(m["rating"])
    return out


def _selfcheck():
    """네트워크를 탄다. `python football/fotmob.py`"""
    games = fixtures()
    assert games, "끝난 EPL 경기가 하나도 없다 — 엔드포인트가 막혔거나 형식이 바뀌었다"
    assert all(len(g["date"]) == 10 for g in games), "날짜 형식이 깨졌다"

    rows = season_rows()
    assert rows, "평점 행이 비었다"
    assert all(0 < r["rating"] <= 10 for r in rows), "평점 범위 밖의 값이 있다"

    players = average(rows)
    # 합계를 다른 경로로 다시 센다 — 선수별 경기 수의 합 = 전체 행 수
    assert sum(p["n"] for p in players) == len(rows)

    standings = table()
    assert len(standings) == 20, f"순위표가 20팀이 아니다: {len(standings)}"
    us = next(r for r in standings if r["team"].startswith("Chelsea"))
    # 변환한 경기로 순위표를 **다시 세서** FotMob 것과 맞춰 본다.
    # 경기 수만 맞춰 보면 원정 스코어가 뒤집혀 있어도 통과한다(실제로 그랬다).
    # 저쪽 순위표는 **진행중 경기를 잠정 반영**하므로 우리도 넣고 센다.
    import epl
    mine = next(r for r in epl.table(as_matches(ongoing=True)) if r["team"] == us["team"])
    for k in ("p", "w", "d", "l", "gf", "ga", "pts"):
        assert mine[k] == us[k], f"{k}: 우리가 센 값 {mine[k]} vs FotMob {us[k]}"

    theirs = squad_ratings()
    pairs = [(p, theirs[p["id"]]) for p in players if p["id"] in theirs and p["n"] >= 2]
    worst = max((abs(p["avg"] - t), p["name"]) for p, t in pairs) if pairs else (0, "")
    print(f"OK  {len(games)}경기 / 평점 {len(rows)}행 / 선수 {len(players)}명")
    print(f"    FotMob 시즌 평점과 대조: {len(pairs)}명, 최대 차이 {worst[0]:.2f} ({worst[1]})"
          + (" — 진행중 경기가 저쪽 평균에만 들어가 있다" if live_match() else ""))
    stats = match_stats(fixtures()[-1]["id"])
    assert len(stats["teams"]) == 2 and stats["rows"], "경기 스탯이 비었다"
    xg = next((r for r in stats["rows"] if "xG" in r["title"]), None)
    assert xg and xg["home_n"] is not None, "xG 를 못 읽었다"
    print(f"    스탯: {stats['teams'][0]} vs {stats['teams'][1]} · "
          + " · ".join(f"{r['title']} {r['home']}-{r['away']}" for r in stats["rows"][:3]))

    avg = season_stats()
    xg_avg = next((r for r in avg if "xG" in r["title"]), None)
    assert xg_avg and xg_avg["n"] == len(fixtures()), "시즌 평균이 경기 수와 안 맞는다"
    print(f"    시즌 평균({xg_avg['n']}경기): "
          + " · ".join(f"{r['title']} {r['ours']:.2f}↔{r['theirs']:.2f}" for r in avg[:3]))

    crests = logos()
    assert crests.get("Chelsea"), "엠블럼 주소를 못 만들었다"
    # 상대 팀 결과를 그 팀 응답에서 직접 센다 — 순위표가 말하는 경기 수와 맞아야 한다
    other = next(r for r in standings if r["team"] != us["team"] and r["p"])
    oid = ids_by_name()[other["team"]]
    theirs_matches = as_matches(oid, ongoing=True)
    recount = next(r for r in epl.table(theirs_matches) if r["team"] == other["team"])
    assert recount["pts"] == other["pts"], (
        f"{other['team']}: 우리가 센 {recount['pts']}점 vs 순위표 {other['pts']}점")
    comps = competitions()
    assert comps, "대회 목록이 비었다"
    later = schedule()
    assert later, "예정 경기가 없다"
    print("    대회: " + ", ".join(f"{c['name']}({c['n']})" for c in comps)
          + f" / 예정 {len(later)}경기, 다음은 {later[0]['round']} {later[0]['date']}")

    now = live_match()
    if now:
        lu = lineup(now["id"])
        print(f"    진행중: vs {now['opp']} {now['score']} ({now['clock']}) · "
              f"{lu.get('formation')} 선발 {len(lu.get('starters', []))}명 "
              f"교체투입 {len(lu.get('subs', []))}명")
    hurt = injuries()
    print(f"    결장/의심 {len(hurt)}명: "
          + ", ".join(f"{h['name']}({h['expected']})" for h in hurt))
    print("    상위: " + ", ".join(f"{p['name']} {p['avg']:.2f}({p['n']}경기)" for p in players[:3]))


if __name__ == "__main__":
    _selfcheck()
