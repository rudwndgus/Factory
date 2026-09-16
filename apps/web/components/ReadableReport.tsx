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
  if (r.kind === "daily_summary" || r.kind === "weekly_summary") {
    return (
      <article className="readable-report production-report">
        <span className="pill">{r.kind === "daily_summary" ? "DAILY" : "WEEKLY"}</span>
        <h2>{r.title}</h2>
        {r.kind === "daily_summary" ? (
          <>
            <dl className="report-summary">
              <div><dt>목표</dt><dd>{r.target}편</dd></div>
              <div><dt>제작 완료</dt><dd>{r.produced}편</dd></div>
              <div><dt>예약 / 게시</dt><dd>{r.scheduled} / {r.published}</dd></div>
              <div><dt>실패</dt><dd>{r.failed}</dd></div>
              <div><dt>대체 작업</dt><dd>{r.replacement_jobs}</dd></div>
              <div><dt>금전 API 지출</dt><dd>$0.00</dd></div>
            </dl>
            <h3>오늘의 주제</h3>
            <ul>{r.topics?.map((item: string) => <li key={item}>{item}</li>)}</ul>
            <h3>대표 검토 필요</h3>
            <ul>{r.videos_requiring_review?.length ? r.videos_requiring_review.map((item: string) => <li key={item}>{item}</li>) : <li>없음</li>}</ul>
          </>
        ) : (
          <>
            <dl className="report-summary">
              <div><dt>게시 영상</dt><dd>{r.published}</dd></div>
              <div><dt>평균 성과 점수</dt><dd>{r.average_performance_score ?? "데이터 대기"}</dd></div>
            </dl>
            <h3>성과 프로필</h3>
            <ul>{r.performance_profiles?.map((item: any) => <li key={`${item.dimension}-${item.value}`}>{item.dimension} · {item.value}: {item.performance_index}× ({item.sample_size}편)</li>)}</ul>
            <h3>다음 실험</h3>
            <ul>{r.recommended_experiments?.map((item: string) => <li key={item}>{item}</li>)}</ul>
          </>
        )}
      </article>
    );
  }
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
      {r.production_summary && (
        <>
          <h3>선정·검증·예약 요약</h3>
          <dl className="report-summary">
            <div><dt>주제 점수</dt><dd>{r.production_summary.topic_score ?? "수동 주제"}</dd></div>
            <div><dt>사실 신뢰도</dt><dd>{r.production_summary.fact_check?.video_fact_confidence ?? "—"}</dd></div>
            <div><dt>검토 정책</dt><dd>{r.production_summary.review_policy || "—"}</dd></div>
            <div><dt>예약 시각</dt><dd>{r.production_summary.scheduled_publish_at ? new Date(r.production_summary.scheduled_publish_at).toLocaleString() : "미정"}</dd></div>
            <div><dt>금전 API 지출</dt><dd>$0.00</dd></div>
          </dl>
          <p>무료 자원 사용: Ollama 로컬 추론 · Kokoro 로컬 음성 · Cloudflare 무료 할당량 · YouTube 쿼터 · 로컬 FFmpeg</p>
          <p>{r.production_summary.topic_selection_reason}</p>
          <details>
            <summary>주제 점수·사실검증 상세</summary>
            <pre>{JSON.stringify({ score: r.production_summary.topic_score_breakdown, fact_check: r.production_summary.fact_check }, null, 2)}</pre>
          </details>
        </>
      )}
      {r.performance_sections?.length > 0 && (
        <>
          <h3>업로드 후 성과·피드백</h3>
          <div className="department-reports">
            {r.performance_sections.map((item: any) => (
              <section key={item.window}>
                <header><strong>{item.window} 분석</strong><span className="pill">{item.performance_score}/100</span></header>
                <p>{item.verdict}</p>
                <p>조회수 {item.views} · 시간당 {item.views_per_hour} · 좋아요율 {(item.like_rate * 100).toFixed(2)}% · 댓글률 {(item.comment_rate * 100).toFixed(2)}%</p>
                <p>평균 시청 {item.average_view_duration}초 · 평균 시청률 {item.average_percentage_viewed}% · 구독자 +{item.subscriber_gain}</p>
                <ul>{item.feedback?.map((line: string) => <li key={line}>{line}</li>)}</ul>
              </section>
            ))}
          </div>
        </>
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
                  {activity.provider && <p className="muted">도구: {activity.provider} · {activity.resource_class} · $0.00</p>}
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
