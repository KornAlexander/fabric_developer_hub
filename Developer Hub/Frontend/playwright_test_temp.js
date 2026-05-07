const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ args: ['--no-sandbox'] });
    const page = await browser.newPage();
    
    console.log('Navigating to http://localhost:60006...');
    // Use a longer timeout for goto and check if it even loads
    await page.goto('http://localhost:60006', { waitUntil: 'domcontentloaded', timeout: 30000 });
    console.log('Page loaded.');

    const client = await page.context().newCDPSession(page);

    // Full screenshot using CDP
    console.log('Capturing full screenshot via CDP...');
    // Increase timeout to 20s for the capture itself
    const captureTimeout = 20000;
    
    const fullScreenshotPromise = client.send('Page.captureScreenshot', {
        format: 'png',
        fromSurface: true
    });
    
    const timeoutPromise = (ms) => new Promise((_, reject) => 
        setTimeout(() => reject(new Error(`Operation timed out after ${ms}ms`)), ms)
    );

    const fullResult = await Promise.race([fullScreenshotPromise, timeoutPromise(captureTimeout)]);
    fs.writeFileSync('/tmp/pw-app-probe-cdp.png', Buffer.from(fullResult.data, 'base64'));
    console.log('Saved /tmp/pw-app-probe-cdp.png');

    // Clipped screenshot using CDP
    console.log('Capturing clipped screenshot via CDP...');
    const clippedScreenshotPromise = client.send('Page.captureScreenshot', {
        format: 'png',
        clip: { x: 0, y: 0, width: 1200, height: 800, scale: 1 },
        fromSurface: true
    });

    const clippedResult = await Promise.race([clippedScreenshotPromise, timeoutPromise(captureTimeout)]);
    fs.writeFileSync('/tmp/pw-app-probe-cdp-clip.png', Buffer.from(clippedResult.data, 'base64'));
    console.log('Saved /tmp/pw-app-probe-cdp-clip.png');

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
