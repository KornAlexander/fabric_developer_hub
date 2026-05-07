const { CleanWebpackPlugin } = require('clean-webpack-plugin');
const CopyWebpackPlugin = require('copy-webpack-plugin');
const HtmlWebpackPlugin = require("html-webpack-plugin");
const Webpack = require("webpack");
const path = require("path");
const fs = require("fs").promises;

// True when running `webpack serve` (dev middleware). We tune the config
// aggressively for fast rebuilds in that case.
const isDevServer = process.env.WEBPACK_SERVE === 'true'
    || process.argv.some(a => a === 'serve');

// Custom console used by webpack's infrastructureLogging so the frontend
// dev-server lines match the backend's format:
//   YYYY-MM-DD HH:MM:SS - <service> - LEVEL - message
// Instead of the default "<i> [webpack-dev-server] 404s…" glyphs which
// are inconsistent with every other log stream in the stack.
function makeFormatter(level, color) {
    const reset = '\x1b[0m';
    const useColor = process.env.NO_COLOR == null
        && (process.stdout.isTTY || process.env.FORCE_COLOR);
    const lvl = useColor ? `${color}${level}${reset}` : level;
    return function (...args) {
        const now = new Date();
        const pad = (n, w = 2) => String(n).padStart(w, '0');
        const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
            + `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
        const msg = args
            .map(a => typeof a === 'string' ? a : require('util').inspect(a, { depth: 2 }))
            .join(' ');
        process.stdout.write(`${ts} - webpack - ${lvl} - ${msg}\n`);
    };
}

const webpackConsole = {
    info: makeFormatter('INFO', '\x1b[38;5;39m'),
    log: makeFormatter('INFO', '\x1b[38;5;39m'),
    debug: makeFormatter('DEBUG', '\x1b[38;5;244m'),
    warn: makeFormatter('WARNING', '\x1b[38;5;214m'),
    error: makeFormatter('ERROR', '\x1b[1;31m'),
    trace: makeFormatter('DEBUG', '\x1b[38;5;244m'),
    group: () => {},
    groupCollapsed: () => {},
    groupEnd: () => {},
    profile: () => {},
    profileEnd: () => {},
    clear: () => {},
    status: makeFormatter('INFO', '\x1b[38;5;39m'),
    Console: console.Console,
    assert: console.assert.bind(console),
    count: () => {},
    countReset: () => {},
    dir: makeFormatter('INFO', '\x1b[38;5;39m'),
    dirxml: makeFormatter('INFO', '\x1b[38;5;39m'),
    table: makeFormatter('INFO', '\x1b[38;5;39m'),
    time: () => {},
    timeEnd: () => {},
    timeLog: () => {},
};

// Route the raw banner lines below through the same formatter so every
// line emitted by the frontend container follows the standard shape.
webpackConsole.info('Build: Environment Variables');
webpackConsole.info(`WORKLOAD_NAME=${process.env.WORKLOAD_NAME}`);
webpackConsole.info(`WORKLOAD_BE_URL=${process.env.WORKLOAD_BE_URL}`);


module.exports = {
    mode: "development",
    entry: "./src/index.ts",
    output: {
        // contenthash keeps unchanged chunks stable across rebuilds so the
        // browser can re-use them instead of redownloading the whole vendor
        // bundle on every edit.
        filename: "bundle.[contenthash].js",
        path: path.resolve(__dirname, "dist"),
        publicPath: '/',
        // Skip emitting absolute request paths as comments — noticeably
        // speeds up emit over ~4k modules and reduces bundle size in dev.
        pathinfo: false,
    },
    // In dev we skip source maps entirely for the first pass — they are the
    // single biggest cost when bundling the ~34 MB Fluent UI tree. Re-enable
    // with WEBPACK_DEV_SOURCEMAPS=1 when you actually need to step through TS.
    devtool: isDevServer
        ? (process.env.WEBPACK_DEV_SOURCEMAPS === '1' ? 'eval-cheap-module-source-map' : 'eval')
        : 'eval-source-map',
    // Persistent filesystem cache. First build is ~full time; subsequent
    // cold starts (e.g. after container restart) drop from ~35s to ~3-5s
    // because parsed/transpiled modules are reused.
    cache: {
        type: 'filesystem',
        cacheDirectory: path.resolve(__dirname, '../.webpack-cache'),
        buildDependencies: {
            config: [__filename],
        },
        // Cap how many rebuild generations webpack keeps in memory before
        // evicting to disk. Default is Infinity, which is the root cause of
        // the slow OOM in webpack-dev-server: every HMR rebuild keeps the
        // previous module graph live. 1 means "only the current build
        // lives in memory" — cold-cache cost is identical, warm rebuilds
        // cost a single disk hit per module, and the heap stays flat.
        maxMemoryGenerations: isDevServer ? 1 : Infinity,
    },
    // Tell webpack not to hash the contents of node_modules on every build —
    // mtime is enough and dramatically cheaper on large trees.
    snapshot: {
        managedPaths: [path.resolve(__dirname, '../node_modules')],
    },
    plugins: [
        // CleanWebpackPlugin wipes output/ on every build. That's fine for
        // CI prod builds, but under webpack-dev-server it defeats caching
        // without any benefit because the dev server serves from memory.
        ...(isDevServer ? [] : [new CleanWebpackPlugin()]),
        new Webpack.DefinePlugin({
            "process.env.WORKLOAD_NAME": JSON.stringify(process.env.WORKLOAD_NAME),
            "process.env.WORKLOAD_BE_URL": JSON.stringify(process.env.WORKLOAD_BE_URL),
        }),
        new HtmlWebpackPlugin({
            template: "./src/index.html",
        }),
        // -- uncomment when static are required to be copied during build --
        new CopyWebpackPlugin({
            patterns: [
                {
                    context: './src/internalAssets/',
                    from: '**/*',
                    to: './internalAssets',
                },
                {
                    from: './tools/web.config',
                    to: './web.config',
                },
                // Ship PDF.js worker as a static asset so PdfPreview can
                // point `GlobalWorkerOptions.workerSrc` at a plain same-
                // origin URL. Avoids the `import.meta.url` resolution
                // footgun under our ES2017 tsconfig target.
                {
                    from: path.resolve(__dirname, '../node_modules/pdfjs-dist/build/pdf.worker.min.mjs'),
                    to: 'pdf.worker.min.js',
                },
            ]
        }),
    ],
    resolve: {
        modules: [__dirname, "src", "node_modules"],
        alias: {
            fonts: path.resolve(__dirname, '../node_modules/katex/dist/fonts'),
        },
        fallback: {
            process: require.resolve('process/browser'),
        },
        extensions: ["*", ".js", ".jsx", ".tsx", ".ts"],
    },
    module: {
        rules: [
            {
                test: /\.tsx?$/,
                exclude: /node_modules/,
                // esbuild-loader transpiles TS/TSX ~10x faster than ts-loader.
                // Types are checked separately (tsc --noEmit) — the dev
                // bundler doesn't need to do it on every keystroke.
                loader: "esbuild-loader",
                options: {
                    loader: "tsx",
                    target: "es2019",
                    tsconfigRaw: require('../tsconfig.json'),
                },
            },
            {
                test: /\.s[ac]ss$/i, // this is for loading scss
                use: ["style-loader", "css-loader", "sass-loader"],
            },
            {
                test: /\.css$/i, // this is for loading plain css (e.g. from node_modules)
                use: ["style-loader", "css-loader"],
            },
            {
                test: /\.(png|jpg|jpeg|svg)$/i, // this is for loading assests
                type: '/asset/resource'
            },
            {
                test: /\.(woff2?|ttf|otf|eot)$/i,
                type: 'asset/resource',
            },
        ],
    },
    // Don't re-walk node_modules on every change — massively reduces CPU
    // on Docker bind mounts where inotify events are expensive.
    watchOptions: {
        ignored: /node_modules/,
        aggregateTimeout: 200,
    },
    stats: isDevServer ? 'minimal' : 'normal',
    infrastructureLogging: isDevServer
        ? { level: 'info', console: webpackConsole }
        : { level: 'info' },
    ignoreWarnings: [
        {
            module: /node_modules\/(@mariozechner\/pi-ai)\/dist\//,
            message: /Critical dependency: the request of a dependency is an expression/,
        },
    ],
    devServer: {
        port: 60006,
        open: false,
        historyApiFallback: true,
        client: {
            // Silence the `[webpack-dev-server] Server started…` and
            // `[HMR] Waiting for update signal from WDS…` banners that
            // the dev-server client prints to the *browser* console —
            // they're injected before our entry bundle runs so our own
            // silencer (installConsoleSilencer) cannot catch them.
            // 'warn' keeps genuine build errors visible.
            logging: 'warn',
            overlay: {
                warnings: false,
                errors: true,
                runtimeErrors: (error) => {
                    if (error?.message?.includes('ResizeObserver')) return false;
                    return true;
                },
            },
        },
        headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
            "Access-Control-Allow-Headers": "*",
            // Chrome Private Network Access (PNA): permits app.powerbi.com
            // (a public/secure origin) to fetch from our loopback dev server.
            // Without this header, Chrome blocks /manifests* preflights with
            // "Permission was denied for this request to access the `loopback`
            // address space" and the Developer Hub tile never appears in the
            // New-item gallery.
            "Access-Control-Allow-Private-Network": "true"
        },
        setupMiddlewares
            : function (middlewares, devServer) {
                webpackConsole.info('Server is listening on port 60006');
                webpackConsole.info('You can now override the Fabric manifest with your own.');

                devServer.app.get('/manifests_new/metadata', function (req, res) {
                    res.writeHead(200, {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET',
                        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                        'Access-Control-Allow-Private-Network': 'true'
                    });
                
                    const devParameters = {
                        name: process.env.WORKLOAD_NAME,
                        url: "http://127.0.0.1:60006",
                        devAADAppConfig: {
                            audience: process.env.DEV_AAD_CONFIG_AUDIENCE || process.env.AUDIENCE,
                            appId: process.env.DEV_AAD_CONFIG_APPID || process.env.CLIENT_ID,
                            redirectUri: process.env.DEV_AAD_CONFIG_REDIRECT_URI || "http://localhost:60006/close"
                        }
                    };
                
                    res.end(JSON.stringify({ extension: devParameters }));
                });

                devServer.app.get('/manifests_new', async function (req, res) {
                    // Serve the single backend-generated manifest package.
                    // NOTE: the path below MUST stay in sync with the generator's
                    // output location (Backend/bin/<Debug|Release>/). It is
                    // asserted in tests/integration/test_manifest_package_bootstrap.py
                    // to prevent the "ManifestPackage not found" regression.
                    const filePath = path.resolve(__dirname, '../../Backend/bin/Debug/ManifestPackage.1.0.0.nupkg');
                    try {
                        // Check if the file exists
                        await fs.access(filePath);
                        
                        res.status(200).set({
                            'Content-Type': 'application/octet-stream',
                            'Content-Disposition': `attachment; filename="ManifestPackage.1.0.0.nupkg"`,
                            'Access-Control-Allow-Origin': '*',
                            'Access-Control-Allow-Methods': 'GET',
                            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                            'Access-Control-Allow-Private-Network': 'true'
                        });
                        
                        res.sendFile(filePath);
                    } catch (err) {
                        webpackConsole.error(`ManifestPackage not found at ${filePath}. Run: cd Backend && python tools/manifest_package_generator.py --version 1.0.0 --project-root .`);
                        res.status(404).json({ error: "ManifestPackage not found. Run the manifest_package_generator in Backend/ first." });
                    }
                });
                return middlewares;
            },
    }
};
