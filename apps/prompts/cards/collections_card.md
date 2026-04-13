# Collections Card

## 컬렉션 매핑
- `ntis_project_v1` = project(과제)
- `ntis_perf_v1` = perf(성과)
- `ntis_supports` = support(QNA / MANUAL)

## 태그 매핑
### project
- `IRD_NAI_PJT_INFO` = 과제

### perf
- `IRD_NAI_RI_PAPER` = 성과-논문
- `IRD_NAI_RI_IPR` = 성과-특허
- `IRD_NAI_RI_SW` = 성과-소프트웨어
- `IRD_NAI_RI_NVR` = 성과-신품종
- `IRD_NAI_RI_ORGSM_INFO` = 성과-생명정보
- `IRD_NAI_RI_ORGSM_RESOURCE` = 성과-생물자원
- `IRD_NAI_RI_COMPOUND` = 성과-화합물
- `IRD_NAI_RI_RSCH_RPT` = 성과-연구보고서
- `IRD_NAI_RI_FCLT_EQUIP` = 성과-시설장비
- `IRD_NAI_RI_TECH_INFO` = 성과-기술요약

### supports
- `QNA`
- `MANUAL`

## 전제
- NTIS 핵심 데이터는 project와 perf 중심이다.
- supports는 도움말 / 매뉴얼 / 고객응대형 정보다.
- project/perf 문서에는 참여인력 `prtcp_mp[]`, 참여기관 `prtcp_org[]` 객체가 포함된다.
