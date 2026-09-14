export default function ReadableReport({ report: r }: { report: any }) {
  if (!r) return <p>보고서를 불러오는 중입니다.</p>;
  if (r.kind === "employee") return <article className="readable-report">
    <span className="pill">담당: {r.role}</span><h2>{r.title}</h2>
    <h3>선정 주제</h3><p>{r.topic}</p><h3>수행 내용 / 다음 단계</h3><p>{r.summary}</p>
    {r.details?.length > 0 && <><h3>작업 내역</h3><ol>{r.details.map((line: string, i: number) => <li key={i}>{line}</li>)}</ol></>}
    <small>작업 번호: {r.video_id}</small>
  </article>;
  return <article className="readable-report"><h2>{r.kind === "weekly" ? "주간" : r.kind === "inspection" ? "시스템 점검" : "일일"} 보고서</h2>
    {r.checks ? <><p>{r.overall}</p><ul>{Object.entries(r.checks).map(([k,v]) => <li key={k}>{k}: {typeof v === "boolean" ? v ? "정상" : "확인 필요" : String(v)}</li>)}</ul></> : <>
      <p>제작 접수 {r.planned}건 · 영상 완성 {r.produced}건 · 테스트 {r.test_runs}건 · 게시 {r.uploaded}건 · 오류 기록 {r.failed}건</p>
      <p>예상 비용 예약액: ${Number(r.estimated_cost || 0).toFixed(2)} — 실제 청구액과 다를 수 있습니다.</p>
      <h3>주제별 진행 상황</h3><ul>{r.videos?.map((v: any, i: number) => <li key={i}>{v.title} — {v.status}</li>)}</ul>
      <h3>다음 할 일</h3><p>미완성 작업과 검수 대기 영상을 확인해주세요. 충분한 게시 성과가 쌓이기 전에는 자동 전략 변경을 하지 않습니다.</p>
    </>}
  </article>;
}
