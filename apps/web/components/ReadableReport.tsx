const roleNames: Record<string, string> = {
  EDITOR: "총괄 편집",
  SCOUT: "소재 발굴",
  RESEARCHER: "자료 조사",
  WRITER: "대본 작성",
  DIRECTOR: "장면 연출",
  ARTIST: "이미지 제작",
  VOICE: "음성·자막",
  CUTTER: "영상 편집",
  "RIGHTS / QC": "권리·품질 검사",
  "ANALYST / UPLOADER": "게시·성과 분석",
};

export default function ReadableReport({ report: r }: { report: any }) {
  if (!r) return <p>보고서를 불러오는 중입니다.</p>;
  return (
    <article className="readable-report production-report">
      <span className="pill">
        {r.archived ? "확인 완료 · 정리함" : "대표 확인 대기"}
      </span>
      <h2>{r.title}</h2>
      <dl className="report-summary">
        <div>
          <dt>영상 주제</dt>
          <dd>{r.topic}</dd>
        </div>
        <div>
          <dt>업로드 날짜</dt>
          <dd>{r.upload_date || "아직 업로드하지 않음"}</dd>
        </div>
        <div>
          <dt>현재 상태</dt>
          <dd>{r.status}</dd>
        </div>
      </dl>
      <h3>전체 특이사항</h3>
      {r.noteworthy?.length ? (
        <ul className="noteworthy">
          {r.noteworthy.map((note: string, i: number) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
      ) : (
        <p>현재 기록된 특이사항이 없습니다.</p>
      )}
      <h3>부서별 수행 내역</h3>
      <div className="department-reports">
        {r.departments?.map((section: any) => (
          <section key={section.role}>
            <header>
              <strong>{roleNames[section.role] || section.role}</strong>
              <span className="pill">{section.status}</span>
            </header>
            {section.activities?.length ? (
              section.activities.map((activity: any, i: number) => (
                <div className="department-activity" key={i}>
                  <h4>{activity.stage}</h4>
                  <p>{activity.summary}</p>
                  {activity.details?.length > 0 && (
                    <ul>
                      {activity.details.map((line: string, j: number) => (
                        <li key={j}>{line}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))
            ) : (
              <p className="muted">아직 수행한 작업이 없습니다.</p>
            )}
            {section.noteworthy?.length > 0 && (
              <p className="department-alert">
                특이사항: {section.noteworthy.join(" · ")}
              </p>
            )}
          </section>
        ))}
      </div>
      {r.confirmed_at && (
        <small>
          확인 시각: {new Date(r.confirmed_at * 1000).toLocaleString()}
        </small>
      )}
    </article>
  );
}
