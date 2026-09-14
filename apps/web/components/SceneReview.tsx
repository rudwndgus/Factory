"use client";
import { useEffect, useState } from "react";
import { config } from "../lib/api";

function SceneImage({ endpoint }: { endpoint: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const c = config();
    const controller = new AbortController();
    let objectUrl = "";
    fetch(c.url + endpoint, { headers: { Authorization: `Bearer ${c.token}` }, signal: controller.signal })
      .then(r => { if (!r.ok) throw Error("Image unavailable"); return r.blob(); })
      .then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(() => {});
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [endpoint]);
  return url ? <img src={url} alt="Scene visual for review" /> : <div className="no-video">이미지 준비 중</div>;
}

export default function SceneReview({ scenes, root, regenerate }: {
  scenes: any[]; root: string; regenerate: (index: number) => void;
}) {
  return <section><h3>장면별 이미지 · 출처 검토</h3>
    <p>AI 이미지는 실제 촬영 자료가 아닌 설명용 이미지입니다. 장면 재생성은 API 비용이 발생하며 최종 영상을 다시 렌더링합니다.</p>
    <div className="scene-review-grid">{scenes.map((scene, index) => <article className="scene-review-card" key={index}>
      <SceneImage endpoint={`${root}/scenes/${index}/image`} />
      <div><span className="pill">장면 {index + 1} · {scene.asset_source === "ai" ? "AI 생성" : scene.asset_source === "external" ? "외부 이미지" : scene.asset_source === "offline_fixture" ? "오프라인 검사 이미지" : "준비 중"}</span>
        <p>{scene.narration}</p><small>{scene.asset_provider} · {scene.visual_style}</small>
        <details><summary>이미지 프롬프트 / 선정 이유</summary><p>{scene.image_prompt}</p><p>{scene.visual_reason}</p></details>
        <button className="btn" onClick={() => { if (window.confirm(`장면 ${index + 1} 이미지만 다시 생성할까요? API 비용이 발생할 수 있습니다.`)) regenerate(index); }}>이 장면 이미지 재생성</button>
      </div>
    </article>)}</div>
  </section>;
}
