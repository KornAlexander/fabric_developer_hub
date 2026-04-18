const { CleanWebpackPlugin } = require('clean-webpack-plugin');
const CopyWebpackPlugin = require('copy-webpack-plugin');
const HtmlWebpackPlugin = require("html-webpack-plugin");
const Webpack = require("webpack");
const path = require("path");
const fs = require("fs").promises;

console.log('******************** Build: Environment Variables *******************');
console.log('process.env.WORKLOAD_NAME: ' + process.env.WORKLOAD_NAME);
console.log('process.env.WORKLOAD_BE_URL: ' + process.env.WORKLOAD_BE_URL);
console.log('*********************************************************************');

module.exports = {
    mode: "development",
    entry: "./src/index.ts",
    output: {
        filename: "bundle.[fullhash].js",
        path: path.resolve(__dirname, "dist"),
        publicPath: '/',
    },
    devtool: "eval-source-map",
    plugins: [
        new CleanWebpackPlugin(),
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
            ]
        }),
    ],
    resolve: {
        modules: [__dirname, "src", "node_modules"],
        extensions: ["*", ".js", ".jsx", ".tsx", ".ts"],
    },
    module: {
        rules: [
            {
                test: /\.tsx?$/,
                exclude: /node_modules/,
                loader: "ts-loader",
                options: {
                    transpileOnly: true,
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
        ],
    },
    devServer: {
        port: 60006,
        open: false,
        historyApiFallback: true,
        client: {
            overlay: {
                runtimeErrors: (error) => {
                    if (error?.message?.includes('ResizeObserver')) return false;
                    return true;
                },
            },
        },
        headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
            "Access-Control-Allow-Headers": "*"
        },
        setupMiddlewares
            : function (middlewares, devServer) {
                console.log('*********************************************************************');
                console.log('****               Server is listening on port 60006             ****');
                console.log('****   You can now override the Fabric manifest with your own.   ****');
                console.log('*********************************************************************');

                devServer.app.get('/manifests_new/metadata', function (req, res) {
                    res.writeHead(200, {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET',
                        'Access-Control-Allow-Headers': 'Content-Type, Authorization'
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
                            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
                        });
                        
                        res.sendFile(filePath);
                    } catch (err) {
                        console.error(`❌ ManifestPackage not found at ${filePath}. Run: cd Backend && python tools/manifest_package_generator.py --version 1.0.0 --project-root .`);
                        res.status(404).json({ error: "ManifestPackage not found. Run the manifest_package_generator in Backend/ first." });
                    }
                });
                return middlewares;
            },
    }
};
