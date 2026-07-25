# CLIPYARD

YouTube 실시간 인기 영상을 국가·영상 길이·Creative Commons 라이선스로 탐색하고, 권리를 보유한 원본 영상에서 9:16 숏폼 클립을 렌더링하는 MVP입니다.

## 실행

```bash
npm install
copy .env.example .env
npm run dev
```

- 웹: `http://localhost:5173`
- API: `http://localhost:8787`
- `YOUTUBE_API_KEY`가 비어 있으면 데모 데이터로 실행됩니다.

## YouTube 실시간 데이터 연결

1. Google Cloud Console에서 프로젝트를 만듭니다.
2. **YouTube Data API v3**를 활성화합니다.
3. API 키를 만들고 HTTP referrer/IP 제한과 API 제한을 설정합니다.
4. `.env`의 `YOUTUBE_API_KEY`에 키를 입력하고 서버를 다시 시작합니다.

실시간 랭킹은 `videos.list(chart=mostPopular)`을 사용합니다. 숏폼 여부는 API에 확정 필드가 없어 현재 길이 180초 이하를 기준으로 표시합니다. CC 표시는 API가 반환한 `status.license=creativeCommon`을 의미하며, 실제 재게시 전에는 원저작자의 조건과 제3자 권리를 별도로 확인해야 합니다.

## 클립 제작

1. 트렌드 카드에서 **클립 만들기**를 선택합니다.
2. 직접 제작했거나 이용 허가를 받은 원본을 업로드합니다.
3. 추천 구간과 화면 맞춤을 선택합니다.
4. 렌더링 후 MP4를 다운로드합니다.

서버는 YouTube 영상을 임의 다운로드하지 않습니다. 업로드 파일은 `storage/uploads`, 결과는 `storage/renders`에 저장되므로 운영 환경에서는 만료 삭제 작업과 오브젝트 스토리지를 연결하세요.

## 프로덕션 빌드

```bash
npm run build
$env:NODE_ENV='production'; npm start
```

## GitHub Pages 버전

`main` 브랜치에 푸시하면 GitHub Actions가 자동으로 Pages에 배포합니다. Pages에서는 서버 API 대신 데모 트렌드 데이터가 표시되며, 원본 영상 분석과 렌더링은 외부 서버 업로드 없이 브라우저 안에서 처리됩니다. 브라우저 렌더 결과는 YouTube가 지원하는 WebM 형식입니다.
