import cors from 'cors'
import 'dotenv/config'
import express from 'express'
import ffmpegPath from 'ffmpeg-static'
import ffprobeStatic from 'ffprobe-static'
import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import multer from 'multer'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const uploadDir = path.join(root, 'storage', 'uploads')
const renderDir = path.join(root, 'storage', 'renders')
fs.mkdirSync(uploadDir, { recursive: true })
fs.mkdirSync(renderDir, { recursive: true })

const app = express()
const port = Number(process.env.PORT || 8787)
app.use(cors())
app.use(express.json({ limit: '1mb' }))
app.use('/renders', express.static(renderDir))

const upload = multer({
  dest: uploadDir,
  limits: { fileSize: 1024 * 1024 * 1024 },
  fileFilter: (_req, file, callback) => {
    callback(null, file.mimetype.startsWith('video/') || file.mimetype.startsWith('audio/'))
  }
})

type RawVideo = {
  id: string
  snippet?: {
    title?: string
    channelTitle?: string
    publishedAt?: string
    thumbnails?: Record<string, { url: string }>
    categoryId?: string
    liveBroadcastContent?: string
  }
  contentDetails?: { duration?: string }
  statistics?: { viewCount?: string; likeCount?: string; commentCount?: string }
  status?: { license?: string; embeddable?: boolean }
}

const demoVideos = [
  {
    id: 'demo-01', title: '낡은 창고를 단 7일 만에 스튜디오로 바꿨습니다', channel: 'BUILD LAB',
    thumbnail: 'https://images.unsplash.com/photo-1497366811353-6870744d04b2?auto=format&fit=crop&w=1200&q=80',
    views: 8240000, likes: 312000, comments: 8490, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 7).toISOString(), duration: 1284, license: 'youtube', category: '라이프', momentum: 97
  },
  {
    id: 'demo-02', title: '도쿄에서 지금 가장 핫한 디저트 5곳', channel: 'MISO TRIP',
    thumbnail: 'https://images.unsplash.com/photo-1554797589-7241bb691973?auto=format&fit=crop&w=1200&q=80',
    views: 6700000, likes: 421000, comments: 12100, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 11).toISOString(), duration: 48, license: 'creativeCommon', category: '여행', momentum: 95
  },
  {
    id: 'demo-03', title: '셰프가 알려주는 완벽한 스테이크의 한 가지 비밀', channel: 'TABLE 101',
    thumbnail: 'https://images.unsplash.com/photo-1558030006-450675393462?auto=format&fit=crop&w=1200&q=80',
    views: 5900000, likes: 198000, comments: 6700, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 16).toISOString(), duration: 723, license: 'creativeCommon', category: '푸드', momentum: 91
  },
  {
    id: 'demo-04', title: 'AI에게 30일 동안 회사를 맡겨봤더니', channel: 'FUTURE OFFICE',
    thumbnail: 'https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&w=1200&q=80',
    views: 4800000, likes: 176000, comments: 9430, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 21).toISOString(), duration: 1562, license: 'youtube', category: '테크', momentum: 88
  },
  {
    id: 'demo-05', title: '눈을 의심하게 되는 농구 트릭샷', channel: 'HOOP DAILY',
    thumbnail: 'https://images.unsplash.com/photo-1546519638-68e109498ffc?auto=format&fit=crop&w=1200&q=80',
    views: 3900000, likes: 286000, comments: 3840, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 27).toISOString(), duration: 39, license: 'youtube', category: '스포츠', momentum: 85
  },
  {
    id: 'demo-06', title: '파리 골목에서 우연히 만난 천재 연주자', channel: 'WALK & LISTEN',
    thumbnail: 'https://images.unsplash.com/photo-1507838153414-b4b713384a76?auto=format&fit=crop&w=1200&q=80',
    views: 3100000, likes: 241000, comments: 5120, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 32).toISOString(), duration: 64, license: 'creativeCommon', category: '음악', momentum: 83
  },
  {
    id: 'demo-07', title: '100년 된 카메라로 뉴욕을 촬영하면 생기는 일', channel: 'FRAME STORY',
    thumbnail: 'https://images.unsplash.com/photo-1452780212940-6f5c0d14d848?auto=format&fit=crop&w=1200&q=80',
    views: 2800000, likes: 132000, comments: 2970, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 38).toISOString(), duration: 901, license: 'creativeCommon', category: '영화/애니', momentum: 78
  },
  {
    id: 'demo-08', title: '강아지가 처음 바다를 본 순간', channel: 'TINY PAWS',
    thumbnail: 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1200&q=80',
    views: 2400000, likes: 340000, comments: 7340, publishedAt: new Date(Date.now() - 1000 * 60 * 60 * 42).toISOString(), duration: 27, license: 'youtube', category: '동물', momentum: 76
  }
]

function isoDurationToSeconds(value = 'PT0S') {
  const match = value.match(/P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/)
  if (!match) return 0
  return Number(match[1] || 0) * 86400 + Number(match[2] || 0) * 3600 + Number(match[3] || 0) * 60 + Number(match[4] || 0)
}

const categoryNames: Record<string, string> = {
  '1': '영화/애니', '2': '자동차', '10': '음악', '15': '동물', '17': '스포츠', '19': '여행',
  '20': '게임', '22': '라이프', '23': '엔터테인먼트', '24': '엔터테인먼트', '25': '뉴스',
  '26': '라이프', '27': '교육', '28': '테크'
}

function normalize(video: RawVideo, index: number) {
  const duration = isoDurationToSeconds(video.contentDetails?.duration)
  const views = Number(video.statistics?.viewCount || 0)
  const likes = Number(video.statistics?.likeCount || 0)
  const ageHours = Math.max(1, (Date.now() - Date.parse(video.snippet?.publishedAt || '')) / 3_600_000)
  const velocity = views / ageHours
  const momentum = Math.min(99, Math.max(45, Math.round(48 + Math.log10(Math.max(10, velocity)) * 10)))
  return {
    id: video.id,
    title: video.snippet?.title || '제목 없음',
    channel: video.snippet?.channelTitle || '알 수 없는 채널',
    thumbnail: video.snippet?.thumbnails?.maxres?.url || video.snippet?.thumbnails?.high?.url || video.snippet?.thumbnails?.medium?.url || '',
    views,
    likes,
    comments: Number(video.statistics?.commentCount || 0),
    publishedAt: video.snippet?.publishedAt,
    duration,
    license: video.status?.license || 'youtube',
    category: categoryNames[video.snippet?.categoryId || ''] || '엔터테인먼트',
    momentum,
    rank: index + 1
  }
}

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, youtubeConnected: Boolean(process.env.YOUTUBE_API_KEY), rendererReady: Boolean(ffmpegPath) })
})

app.get('/api/trending', async (req, res) => {
  const region = String(req.query.region || 'KR').toUpperCase()
  const maxResults = Math.min(50, Math.max(8, Number(req.query.limit || 24)))
  const key = process.env.YOUTUBE_API_KEY

  if (!key) {
    return res.json({ source: 'demo', region, updatedAt: new Date().toISOString(), items: demoVideos.map((video, index) => ({ ...video, rank: index + 1 })) })
  }

  try {
    const params = new URLSearchParams({
      part: 'snippet,contentDetails,statistics,status', chart: 'mostPopular', regionCode: region,
      maxResults: String(maxResults), key
    })
    const response = await fetch(`https://www.googleapis.com/youtube/v3/videos?${params}`)
    const data = await response.json() as { items?: RawVideo[]; error?: { message?: string } }
    if (!response.ok) throw new Error(data.error?.message || 'YouTube API 요청 실패')
    res.json({ source: 'youtube', region, updatedAt: new Date().toISOString(), items: (data.items || []).map(normalize) })
  } catch (error) {
    res.status(502).json({ error: error instanceof Error ? error.message : '트렌드 데이터를 불러오지 못했습니다.' })
  }
})

function probeDuration(inputPath: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(ffprobeStatic.path, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', inputPath])
    let output = ''
    child.stdout.on('data', chunk => { output += chunk })
    child.on('error', reject)
    child.on('close', code => code === 0 ? resolve(Number(output.trim())) : reject(new Error('영상 길이를 확인하지 못했습니다.')))
  })
}

app.post('/api/media/analyze', upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: '영상 파일을 선택해 주세요.' })
  try {
    const duration = await probeDuration(req.file.path)
    const clipLength = Math.min(60, Math.max(20, Number(req.body.clipLength || 35)))
    const count = Math.min(5, Math.max(1, Math.floor(duration / Math.max(clipLength * 2, 1))))
    const safeDuration = Math.max(clipLength, duration)
    const candidates = Array.from({ length: count }, (_, index) => {
      const start = Math.max(0, ((safeDuration - clipLength) * (index + 1)) / (count + 1))
      return {
        id: `${req.file!.filename}-${index + 1}`,
        label: index === 0 ? '가장 강한 오프닝' : index === 1 ? '핵심 인사이트' : `하이라이트 ${index + 1}`,
        start: Math.round(start * 10) / 10,
        end: Math.round(Math.min(duration, start + clipLength) * 10) / 10,
        score: Math.max(72, 94 - index * 5)
      }
    })
    res.json({ uploadId: req.file.filename, originalName: req.file.originalname, duration, candidates })
  } catch (error) {
    fs.rm(req.file.path, { force: true }, () => undefined)
    res.status(422).json({ error: error instanceof Error ? error.message : '영상 분석에 실패했습니다.' })
  }
})

app.post('/api/media/render', async (req, res) => {
  const { uploadId, start = 0, end = 35, layout = 'fill' } = req.body as { uploadId?: string; start?: number; end?: number; layout?: string }
  if (!uploadId || !/^[\w-]+$/.test(uploadId)) return res.status(400).json({ error: '올바른 업로드 ID가 필요합니다.' })
  const inputPath = path.join(uploadDir, uploadId)
  if (!fs.existsSync(inputPath)) return res.status(404).json({ error: '원본 파일을 찾을 수 없습니다. 다시 업로드해 주세요.' })
  const clipDuration = Math.min(180, Math.max(3, Number(end) - Number(start)))
  const outputName = `clip-${Date.now()}-${uploadId.slice(0, 8)}.mp4`
  const outputPath = path.join(renderDir, outputName)
  const fillFilter = 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920'
  const fitFilter = 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x111111'
  const args = [
    '-y', '-ss', String(Math.max(0, Number(start))), '-i', inputPath, '-t', String(clipDuration),
    '-vf', layout === 'fit' ? fitFilter : fillFilter,
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-c:a', 'aac', '-b:a', '160k',
    '-movflags', '+faststart', outputPath
  ]
  try {
    await new Promise<void>((resolve, reject) => {
      if (!ffmpegPath) return reject(new Error('FFmpeg 실행 파일을 찾을 수 없습니다.'))
      const child = spawn(ffmpegPath, args)
      let stderr = ''
      child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000) })
      child.on('error', reject)
      child.on('close', code => code === 0 ? resolve() : reject(new Error(`렌더링 실패 (${code}): ${stderr.slice(-300)}`)))
    })
    res.json({ ok: true, downloadUrl: `/renders/${outputName}`, filename: outputName })
  } catch (error) {
    fs.rm(outputPath, { force: true }, () => undefined)
    res.status(500).json({ error: error instanceof Error ? error.message : '렌더링에 실패했습니다.' })
  }
})

if (process.env.NODE_ENV === 'production' && fs.existsSync(path.join(root, 'dist'))) {
  app.use(express.static(path.join(root, 'dist')))
  app.get('/{*splat}', (_req, res) => res.sendFile(path.join(root, 'dist', 'index.html')))
}

app.listen(port, () => {
  console.log(`CLIPYARD API running on http://localhost:${port}`)
})
