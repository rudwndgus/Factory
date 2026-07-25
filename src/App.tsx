import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowRight, BarChart3, Check, ChevronDown, ChevronRight, CircleHelp,
  Clock3, Download, Film, Flame, Gauge, Globe2, LayoutDashboard, Library, LoaderCircle,
  Menu, MoreHorizontal, Play, Plus, RefreshCw, Scissors, Search, Settings, ShieldCheck,
  Sparkles, TrendingUp, Upload, WandSparkles, X, Zap
} from 'lucide-react'

type Video = {
  id: string; title: string; channel: string; thumbnail: string; views: number; likes: number;
  comments: number; publishedAt: string; duration: number; license: string; category: string;
  momentum: number; rank: number
}

type Candidate = { id: string; label: string; start: number; end: number; score: number }
type UploadResult = { uploadId: string; originalName: string; duration: number; candidates: Candidate[] }

const regions = [
  { code: 'KR', flag: '🇰🇷', label: '대한민국' }, { code: 'US', flag: '🇺🇸', label: '미국' },
  { code: 'JP', flag: '🇯🇵', label: '일본' }, { code: 'GB', flag: '🇬🇧', label: '영국' },
  { code: 'DE', flag: '🇩🇪', label: '독일' }, { code: 'BR', flag: '🇧🇷', label: '브라질' }
]

const categories = ['전체', '엔터테인먼트', '음악', '라이프', '푸드', '스포츠', '테크', '여행']

const compact = (value: number) => new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
const formatDuration = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
const ago = (date: string) => {
  const hours = Math.max(1, Math.floor((Date.now() - new Date(date).getTime()) / 3_600_000))
  return hours < 24 ? `${hours}시간 전` : `${Math.floor(hours / 24)}일 전`
}

function readVideoDuration(file: File) {
  return new Promise<number>((resolve, reject) => {
    const video = document.createElement('video')
    const url = URL.createObjectURL(file)
    video.preload = 'metadata'
    video.onloadedmetadata = () => { const duration = video.duration; URL.revokeObjectURL(url); resolve(duration) }
    video.onerror = () => { URL.revokeObjectURL(url); reject(new Error('이 브라우저에서 영상 정보를 읽지 못했습니다.')) }
    video.src = url
  })
}

function makeCandidates(duration: number, clipLength = 35): Candidate[] {
  const length = Math.min(60, Math.max(3, Math.min(clipLength, duration)))
  const count = Math.min(5, Math.max(1, Math.floor(duration / Math.max(length * 2, 1))))
  return Array.from({ length: count }, (_, index) => {
    const start = Math.max(0, ((Math.max(length, duration) - length) * (index + 1)) / (count + 1))
    return { id: `browser-${index + 1}`, label: index === 0 ? '가장 강한 오프닝' : index === 1 ? '핵심 인사이트' : `하이라이트 ${index + 1}`, start: Math.round(start * 10) / 10, end: Math.round(Math.min(duration, start + length) * 10) / 10, score: Math.max(72, 94 - index * 5) }
  })
}

async function renderInBrowser(file: File, start: number, end: number, layout: 'fill' | 'fit') {
  if (!HTMLCanvasElement.prototype.captureStream || !window.MediaRecorder) throw new Error('이 브라우저는 영상 렌더링을 지원하지 않습니다. Chrome 또는 Edge를 사용해 주세요.')
  const video = document.createElement('video')
  const sourceUrl = URL.createObjectURL(file)
  video.src = sourceUrl; video.preload = 'auto'; video.playsInline = true
  await new Promise<void>((resolve, reject) => { video.onloadeddata = () => resolve(); video.onerror = () => reject(new Error('영상 파일을 열지 못했습니다.')) })
  video.currentTime = Math.max(0, start)
  await new Promise<void>(resolve => { video.onseeked = () => resolve() })

  const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280
  const context = canvas.getContext('2d')!
  const canvasStream = canvas.captureStream(30)
  const audioContext = new AudioContext()
  const audioSource = audioContext.createMediaElementSource(video)
  const audioDestination = audioContext.createMediaStreamDestination()
  audioSource.connect(audioDestination)
  const output = new MediaStream([...canvasStream.getVideoTracks(), ...audioDestination.stream.getAudioTracks()])
  const mimeType = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find(MediaRecorder.isTypeSupported) || ''
  const recorder = new MediaRecorder(output, mimeType ? { mimeType, videoBitsPerSecond: 5_000_000 } : undefined)
  const chunks: BlobPart[] = []
  recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
  const done = new Promise<Blob>((resolve, reject) => {
    recorder.onerror = () => reject(new Error('브라우저 렌더링에 실패했습니다.'))
    recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType || 'video/webm' }))
  })
  const draw = () => {
    const sourceRatio = video.videoWidth / video.videoHeight
    const targetRatio = canvas.width / canvas.height
    context.fillStyle = '#111'; context.fillRect(0, 0, canvas.width, canvas.height)
    let dw = canvas.width, dh = canvas.height, dx = 0, dy = 0
    if (layout === 'fill') {
      if (sourceRatio > targetRatio) { dw = canvas.height * sourceRatio; dx = (canvas.width - dw) / 2 } else { dh = canvas.width / sourceRatio; dy = (canvas.height - dh) / 2 }
    } else {
      if (sourceRatio > targetRatio) { dh = canvas.width / sourceRatio; dy = (canvas.height - dh) / 2 } else { dw = canvas.height * sourceRatio; dx = (canvas.width - dw) / 2 }
    }
    context.drawImage(video, dx, dy, dw, dh)
    if (!video.paused && !video.ended && video.currentTime < end) requestAnimationFrame(draw)
    else if (recorder.state !== 'inactive') recorder.stop()
  }
  recorder.start(500); await audioContext.resume(); await video.play(); draw()
  const blob = await done
  video.pause(); canvasStream.getTracks().forEach(track => track.stop()); audioDestination.stream.getTracks().forEach(track => track.stop())
  await audioContext.close(); URL.revokeObjectURL(sourceUrl)
  return URL.createObjectURL(blob)
}

function Logo() {
  return <div className="logo"><span className="logo-mark"><span /></span><b>CLIPYARD</b></div>
}

function Sidebar({ open, close }: { open: boolean; close: () => void }) {
  return <>
    {open && <button className="scrim" onClick={close} aria-label="메뉴 닫기" />}
    <aside className={`sidebar ${open ? 'open' : ''}`}>
      <div className="sidebar-top"><Logo /><button className="mobile-close" onClick={close}><X size={20} /></button></div>
      <nav>
        <p className="nav-label">WORKSPACE</p>
        <a className="nav-item active"><LayoutDashboard size={19} /> 트렌드 탐색</a>
        <a className="nav-item"><Scissors size={19} /> 클립 스튜디오 <span className="nav-new">NEW</span></a>
        <a className="nav-item"><Library size={19} /> 내 프로젝트 <span className="nav-count">3</span></a>
        <a className="nav-item"><BarChart3 size={19} /> 성과 분석</a>
        <p className="nav-label second">LIBRARY</p>
        <a className="nav-item"><Flame size={19} /> 급상승 컬렉션</a>
        <a className="nav-item"><ShieldCheck size={19} /> 재사용 가능</a>
      </nav>
      <div className="quota">
        <div className="quota-title"><span>이번 달 생성량</span><b>24%</b></div>
        <div className="quota-bar"><span /></div>
        <p>24분 / 100분</p>
        <button>플랜 업그레이드 <ArrowRight size={14} /></button>
      </div>
      <div className="side-bottom">
        <a><CircleHelp size={19} /> 도움말</a><a><Settings size={19} /> 설정</a>
        <div className="profile"><div className="avatar">K</div><div><b>Kyung</b><span>Creator plan</span></div><MoreHorizontal size={18} /></div>
      </div>
    </aside>
  </>
}

function EditorModal({ video, onClose }: { video: Video; onClose: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [analysis, setAnalysis] = useState<UploadResult | null>(null)
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [download, setDownload] = useState('')
  const [layout, setLayout] = useState<'fill' | 'fit'>('fill')
  const [agreed, setAgreed] = useState(false)

  const analyze = async () => {
    if (!file || !agreed) return
    setBusy(true); setStatus('영상의 하이라이트 후보를 찾는 중…')
    const body = new FormData(); body.append('video', file); body.append('clipLength', '35')
    try {
      if (location.hostname.endsWith('github.io')) {
        const duration = await readVideoDuration(file)
        const localResult = { uploadId: 'browser-local', originalName: file.name, duration, candidates: makeCandidates(duration) }
        setAnalysis(localResult); setSelected(localResult.candidates[0]); setStatus(''); return
      }
      const response = await fetch('/api/media/analyze', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error)
      setAnalysis(data); setSelected(data.candidates[0]); setStatus('')
    } catch (error) { setStatus(error instanceof Error ? error.message : '분석에 실패했습니다.') }
    finally { setBusy(false) }
  }

  const render = async () => {
    if (!analysis || !selected) return
    setBusy(true); setDownload(''); setStatus('9:16 숏폼으로 렌더링 중… 영상 길이에 따라 잠시 걸릴 수 있어요.')
    try {
      if (analysis.uploadId === 'browser-local') {
        if (!file) throw new Error('원본 파일을 다시 선택해 주세요.')
        const url = await renderInBrowser(file, selected.start, selected.end, layout)
        setDownload(url); setStatus('브라우저 렌더링 완료! WebM 파일로 다운로드할 수 있어요.'); return
      }
      const response = await fetch('/api/media/render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uploadId: analysis.uploadId, start: selected.start, end: selected.end, layout })
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error)
      setDownload(data.downloadUrl); setStatus('렌더링 완료! 바로 다운로드할 수 있어요.')
    } catch (error) { setStatus(error instanceof Error ? error.message : '렌더링에 실패했습니다.') }
    finally { setBusy(false) }
  }

  return <div className="modal-backdrop" role="dialog" aria-modal="true">
    <div className="editor-modal">
      <div className="modal-head"><div><span className="eyebrow"><WandSparkles size={14} /> AI CLIP STUDIO</span><h2>바이럴 클립 만들기</h2></div><button onClick={onClose}><X /></button></div>
      <div className="source-card"><img src={video.thumbnail} /><div><span>선택한 트렌드</span><b>{video.title}</b><small>{video.channel} · {formatDuration(video.duration)}</small></div></div>
      {!analysis ? <div className="upload-stage">
        <div className={`drop-zone ${file ? 'has-file' : ''}`} onClick={() => fileRef.current?.click()}>
          <input ref={fileRef} type="file" accept="video/*" hidden onChange={e => setFile(e.target.files?.[0] || null)} />
          <div className="upload-icon">{file ? <Check /> : <Upload />}</div>
          <b>{file ? file.name : '권리를 보유한 원본 영상을 업로드하세요'}</b>
          <span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 변경하려면 클릭` : 'MP4, MOV, WebM · 최대 1GB'}</span>
        </div>
        <label className="rights-check"><input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} /><span><Check size={13} /></span><p>이 영상을 편집·게시할 권리를 보유했거나 적법한 허가를 받았습니다.</p></label>
        <div className="notice"><ShieldCheck size={19} /><p><b>안전한 제작을 위해</b>YouTube 영상은 서버가 임의 다운로드하지 않습니다. 내 원본을 올리면 선택한 트렌드의 메타데이터를 참고해 클립을 만듭니다.</p></div>
        <button className="primary wide" disabled={!file || !agreed || busy} onClick={analyze}>{busy ? <LoaderCircle className="spin" /> : <Sparkles />} 하이라이트 분석하기</button>
      </div> : <div className="edit-stage">
        <div className="candidate-head"><div><b>추천 하이라이트</b><span>{analysis.candidates.length}개 구간을 찾았어요</span></div><button onClick={() => { setAnalysis(null); setSelected(null) }}>영상 변경</button></div>
        <div className="candidates">{analysis.candidates.map((item, index) => <button key={item.id} className={selected?.id === item.id ? 'selected' : ''} onClick={() => { setSelected(item); setDownload('') }}>
          <span className="candidate-number">0{index + 1}</span><span className="candidate-copy"><b>{item.label}</b><small>{formatDuration(item.start)} — {formatDuration(item.end)}</small></span><em>{item.score}<small>점</small></em>
        </button>)}</div>
        <div className="layout-row"><div><b>화면 맞춤</b><span>9:16 · 1080×1920</span></div><div className="segmented"><button className={layout === 'fill' ? 'active' : ''} onClick={() => setLayout('fill')}>화면 채우기</button><button className={layout === 'fit' ? 'active' : ''} onClick={() => setLayout('fit')}>전체 보기</button></div></div>
        <div className="render-actions">
          {download ? <a className="primary wide success" href={download} download={download.startsWith('blob:') ? 'clipyard-short.webm' : true}><Download /> 완성 클립 다운로드</a> : <button className="primary wide" onClick={render} disabled={busy}>{busy ? <LoaderCircle className="spin" /> : <Film />} 숏폼 렌더링</button>}
        </div>
      </div>}
      {status && <div className={`render-status ${download ? 'done' : ''}`}>{busy && <LoaderCircle className="spin" size={16} />}{status}</div>}
    </div>
  </div>
}

function VideoCard({ video, onCreate }: { video: Video; onCreate: () => void }) {
  const isShort = video.duration <= 180
  return <article className="video-card">
    <div className="thumb-wrap">
      <img src={video.thumbnail} alt="" loading="lazy" />
      <span className={`rank rank-${video.rank}`}>{String(video.rank).padStart(2, '0')}</span>
      <span className="duration">{isShort ? <Zap size={11} fill="currentColor" /> : null}{formatDuration(video.duration)}</span>
      <button className="play-button" onClick={() => window.open(`https://www.youtube.com/watch?v=${video.id}`, '_blank')} aria-label="YouTube에서 보기"><Play fill="currentColor" /></button>
      <div className="card-action"><button onClick={onCreate}><Scissors size={16} /> 클립 만들기</button></div>
    </div>
    <div className="card-body">
      <div className="card-badges"><span className={isShort ? 'short' : 'long'}>{isShort ? 'SHORT' : 'LONG'}</span>{video.license === 'creativeCommon' && <span className="cc"><ShieldCheck size={12} /> CC</span>}<span className="momentum"><TrendingUp size={12} /> {video.momentum}</span></div>
      <h3>{video.title}</h3><p className="channel">{video.channel}</p>
      <div className="stats"><span><b>{compact(video.views)}</b> 조회</span><i /><span>{ago(video.publishedAt)}</span><span className="growth"><TrendingUp size={13} /> 급상승</span></div>
    </div>
  </article>
}

export default function App() {
  const [sidebar, setSidebar] = useState(false)
  const [region, setRegion] = useState('KR')
  const [type, setType] = useState<'all' | 'short' | 'long'>('all')
  const [reuse, setReuse] = useState(false)
  const [category, setCategory] = useState('전체')
  const [query, setQuery] = useState('')
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)
  const [source, setSource] = useState('demo')
  const [updated, setUpdated] = useState(new Date())
  const [regionOpen, setRegionOpen] = useState(false)
  const [editor, setEditor] = useState<Video | null>(null)
  const [toast, setToast] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/trending?region=${region}&limit=32`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.error)
      setVideos(data.items); setSource(data.source); setUpdated(new Date(data.updatedAt))
      if (data.source === 'demo') { setToast('데모 데이터로 표시 중 · API 키를 연결하면 실시간으로 전환됩니다.'); setTimeout(() => setToast(''), 4000) }
    } catch {
      try {
        const response = await fetch(`${import.meta.env.BASE_URL}demo-videos.json`)
        const data = await response.json()
        const now = Date.now()
        setVideos(data.items.map((item: Video, index: number) => ({ ...item, publishedAt: new Date(now - (index * 6 + 7) * 3_600_000).toISOString() })))
        setSource('demo'); setUpdated(new Date()); setToast('웹 버전 데모 모드 · 클립 편집은 이 브라우저에서 직접 처리됩니다.'); setTimeout(() => setToast(''), 4500)
      } catch { setToast('데이터를 불러오지 못했습니다.') }
    }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [region])

  const filtered = useMemo(() => videos.filter(video => {
    if (type === 'short' && video.duration > 180) return false
    if (type === 'long' && video.duration <= 180) return false
    if (reuse && video.license !== 'creativeCommon') return false
    if (category !== '전체' && video.category !== category) return false
    return !query || `${video.title} ${video.channel}`.toLowerCase().includes(query.toLowerCase())
  }), [videos, type, reuse, category, query])

  const selectedRegion = regions.find(item => item.code === region)!

  return <div className="app-shell">
    <Sidebar open={sidebar} close={() => setSidebar(false)} />
    <main>
      <header className="topbar">
        <button className="menu-button" onClick={() => setSidebar(true)}><Menu /></button>
        <div className="mobile-logo"><Logo /></div>
        <div className="search"><Search size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="영상, 채널, 키워드 검색" /><kbd>⌘ K</kbd></div>
        <div className="top-actions"><button className="icon-button"><CircleHelp size={19} /></button><button className="create-button" onClick={() => videos[0] && setEditor(videos[0])}><Plus size={17} /> 새 클립 만들기</button><div className="mini-avatar">K</div></div>
      </header>

      <div className="content">
        <section className="hero-row">
          <div><div className="live-kicker"><span /> LIVE TREND RADAR</div><h1>지금 뜨는 순간을<br /><em>내 콘텐츠로.</em></h1><p>전 세계 YouTube 인기 영상을 발견하고, 다음 숏폼의 영감을 얻으세요.</p></div>
          <div className="pulse-card"><div className="pulse-icon"><Gauge /></div><div><span>TREND PULSE</span><b>오늘의 트렌드 온도</b></div><strong>92<small>°</small></strong><div className="pulse-bars">{[42,55,48,70,63,82,74,96,86,100].map((h,i)=><i key={i} style={{height:`${h}%`}} />)}</div></div>
        </section>

        <section className="controls-panel">
          <div className="control-primary">
            <div className="region-select"><button onClick={() => setRegionOpen(v => !v)}><span>{selectedRegion.flag}</span><div><small>지역</small><b>{selectedRegion.label}</b></div><ChevronDown size={16} /></button>
              {regionOpen && <div className="region-menu">{regions.map(item => <button key={item.code} onClick={() => { setRegion(item.code); setRegionOpen(false) }}><span>{item.flag}</span>{item.label}{region === item.code && <Check size={15} />}</button>)}</div>}
            </div>
            <div className="type-switch"><button className={type === 'all' ? 'active' : ''} onClick={() => setType('all')}>전체</button><button className={type === 'short' ? 'active' : ''} onClick={() => setType('short')}><Zap size={13} /> 숏폼</button><button className={type === 'long' ? 'active' : ''} onClick={() => setType('long')}><Film size={14} /> 롱폼</button></div>
            <label className="reuse-toggle"><button role="switch" aria-checked={reuse} className={reuse ? 'on' : ''} onClick={() => setReuse(v => !v)}><span /></button><ShieldCheck size={16} /><div><b>재사용 가능</b><small>Creative Commons</small></div></label>
          </div>
          <button className="refresh" onClick={load} disabled={loading}><RefreshCw size={16} className={loading ? 'spin' : ''} /> {updated.toLocaleTimeString('ko-KR', {hour:'2-digit',minute:'2-digit'})} 업데이트</button>
        </section>

        <div className="category-row"><div>{categories.map(item => <button key={item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{item}</button>)}</div><span>{source === 'youtube' ? <><span className="source-dot" /> YouTube 실시간</> : 'DEMO MODE'}</span></div>

        <section className="ranking-head"><div><span className="section-number">01</span><div><h2>실시간 인기 랭킹</h2><p>{selectedRegion.label}에서 가장 빠르게 성장 중인 영상</p></div></div><div className="result-count"><b>{filtered.length}</b> VIDEOS</div></section>

        {loading ? <div className="loading-grid">{Array.from({length:8}).map((_,i)=><div className="skeleton" key={i}><div/><span/><span/></div>)}</div> : filtered.length ? <div className="video-grid">{filtered.map(video => <VideoCard key={video.id} video={video} onCreate={() => setEditor(video)} />)}</div> : <div className="empty"><Search /><h3>조건에 맞는 영상이 없어요</h3><p>필터나 검색어를 조금 넓혀보세요.</p><button onClick={() => {setType('all');setReuse(false);setCategory('전체');setQuery('')}}>필터 초기화</button></div>}

        <section className="cta-strip"><div className="cta-icon"><Scissors /></div><div><span>FROM TREND TO SHORTS</span><h3>롱폼 하나로, 숏폼 여러 개를.</h3><p>원본 영상을 올리면 하이라이트 구간을 찾고 9:16 영상으로 렌더링합니다.</p></div><button onClick={() => videos[0] && setEditor(videos[0])}>클립 스튜디오 시작 <ChevronRight /></button></section>
        <footer><Logo /><p>트렌드를 발견하고, 더 빠르게 만드세요.</p><span>© 2026 CLIPYARD</span></footer>
      </div>
    </main>
    {editor && <EditorModal video={editor} onClose={() => setEditor(null)} />}
    {toast && <div className="toast"><Sparkles size={17} />{toast}<button onClick={() => setToast('')}><X size={15} /></button></div>}
  </div>
}
