const { chromium } = require('playwright');
const path = require('path');

(async () => {
    let browserContext;
    try {
        browserContext = await chromium.launchPersistentContext('/home/lukaszobst/.config/chromium-wsl', {
            headless: true,
            args: ['--no-sandbox'],
            viewport: { width: 1920, height: 1080 }
        });
    } catch (e) {
        console.error('Failed to launch browser (likely profile locked):', e.message);
        process.exit(1);
    }

    const page = await browserContext.newPage();
    const url = 'https://app.powerbi.com/groups/8bdca8af-1db1-4fd8-9564-0c98b4dbdffc/reports/cae2e0c7-af9d-43bb-82ee-8722c7a7b1b2?ctid=bfccc183-b152-43b7-babd-7feaa07557d1&experience=fabric-developer';
    const screenshotPath = '/home/lukaszobst/Fabric ClawHub/Developer Hub/docs/screenshots/fabric-inventory-report-live.png';

    try {
        console.log('Navigating to URL...');
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        console.log('Waiting for report to load...');
        const startTime = Date.now();
        while (Date.now() - startTime < 120000) {
            const bodyText = await page.innerText('body');
            if (bodyText.includes('Something went wrong') || 
                bodyText.includes('Failed to get access request info') || 
                bodyText.includes('Sign in')) {
                console.error('Error detected in page content.');
                console.log('Body Text Excerpt:', bodyText.substring(0, 1000));
                process.exit(1);
            }
            if (!bodyText.includes('Loading your report')) {
                break;
            }
            await new Promise(r => setTimeout(r, 2000));
        }

        await new Promise(r => setTimeout(r, 10000));

        const finalUrl = page.url();
        const title = await page.title();
        const bodyText = await page.innerText('body');
        
        console.log('Taking screenshot...');
        // Set timeout to 0 (no timeout) for the screenshot and disable animations
        await page.screenshot({ path: screenshotPath, fullPage: true, timeout: 0, animations: 'disabled' });

        console.log('Title:', title);
        console.log('Final URL:', finalUrl);
        console.log('Screenshot Path:', screenshotPath);
        console.log('Body Text Excerpt:', bodyText.substring(0, 1000));
    } catch (e) {
        console.error('An error occurred during Playwright execution:', e.message);
        process.exit(1);
    } finally {
        await browserContext.close();
    }
})();
