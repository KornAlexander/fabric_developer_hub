const fs = require('fs');
const https = require('https');
const { execSync } = require('child_process');

const WORKSPACE_ID = "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc";
const REPORT_ID = "cae2e0c7-af9d-43bb-82ee-8722c7a7b1b2";
const EXPORT_ID = "MS9CbG9iSWRWMi0yODFjNGZhZi1iZGY1LTRmNGItODdiYy1jMmMyZDEyNjk2MjM4QlFUcnBXZ2lHWjhOQUJ0dEJMUlV5NUZ1WlZOWVFtNGhFVWs4NDgybE4wPS4=";
const PDF_PATH = "/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.pdf";
const PNG_PATH = "/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.png";

async function run() {
    console.log("Getting Power BI token...");
    const token = execSync('az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv').toString().trim();

    const options = {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    };

    const statusUrl = `https://api.powerbi.com/v1.0/myorg/groups/${WORKSPACE_ID}/reports/${REPORT_ID}/exports/${EXPORT_ID}`;
    
    console.log("Checking status...");
    const checkStatus = () => new Promise((resolve, reject) => {
        https.get(statusUrl, options, (res) => {
            let data = '';
            res.on('data', (chunk) => data += chunk);
            res.on('end', () => resolve(JSON.parse(data)));
        }).on('error', reject);
    });

    const statusData = await checkStatus();
    console.log("Status JSON:", JSON.stringify(statusData, null, 2));

    if (statusData.status === 'Succeeded') {
        console.log("Downloading file...");
        const fileUrl = `${statusUrl}/file`;
        const file = fs.createWriteStream(PDF_PATH);
        
        const download = () => new Promise((resolve, reject) => {
            https.get(fileUrl, options, (res) => {
                res.pipe(file);
                file.on('finish', () => {
                    file.close();
                    resolve();
                });
            }).on('error', reject);
        });

        await download();
        console.log("Download complete.");
        
        console.log("ls -l output:");
        console.log(execSync(`ls -l "${PDF_PATH}"`).toString());
        
        console.log("file output:");
        console.log(execSync(`file "${PDF_PATH}"`).toString());

        try {
            console.log("Converting to PNG...");
            execSync(`pdftoppm -f 1 -l 1 -png "${PDF_PATH}" "/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live"`);
            execSync(`mv "/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live-1.png" "${PNG_PATH}"`);
            console.log("Conversion complete.");
            console.log(execSync(`ls -l "${PNG_PATH}"`).toString());
            console.log(execSync(`file "${PNG_PATH}"`).toString());
        } catch (e) {
            console.log("Conversion failed or pdftoppm not found:", e.message);
        }
    } else {
        console.log("Export not succeeded yet or failed.");
    }
}

run();
