const basePath = process.env.PAGES_BUILD === "true" ? "/Factory" : "";
export default {
  output: "export",
  basePath,
  images: { unoptimized: true },
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
};
