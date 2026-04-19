// IMPORTANT: `./logging` must be the very first import — its module
// body installs a console silencer, and ES-module imports are
// evaluated in declaration order. Anything imported above it gets to
// print to the console before we've silenced it (that's how the
// i18next banner was still slipping through).
import './logging';
import { bootstrap } from '@ms-fabric/workload-client';
import './i18n';

function printFormattedAADErrorMessage(hashMessage: string): void {
    const hashString = hashMessage.slice(1);

    // Decode URL encoding and parse key-value pairs
    const searchParams = new URLSearchParams(hashString);
    const formattedMessage: Record<string, string> = {};

    searchParams.forEach((value, key) => {
        formattedMessage[key] = decodeURIComponent(value);
    });

    // Print formatted message
    // Use textContent (not innerHTML) — the AAD-returned error hash is
    // untrusted user input and must never be rendered as HTML.
    document.documentElement.textContent = "There was a problem with the consent, open browser debug console for more details";
    for (const key in formattedMessage) {
        if (Object.prototype.hasOwnProperty.call(formattedMessage, key)) {
            console.log(`${key}: ${formattedMessage[key]}`);
        }
    }
}

/** This is used for authentication API as a redirect URI.
 * Delete this code if you do not plan on using authentication API.
 * You can change the redirectUriPath to whatever suits you.
 */
const redirectUriPath = '/close';
const url = new URL(window.location.href);
if (url.pathname?.startsWith(redirectUriPath)) {
    // Handle errors, Please refer to https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes
    if (url?.hash?.includes("error")) {
        // Handle missing service principal error
        if (url.hash.includes("AADSTS650052")) {
            printFormattedAADErrorMessage(url?.hash);
            // handle user declined the consent error
        } else if (url.hash.includes("AADSTS65004")) {
            printFormattedAADErrorMessage(url?.hash);
        } else {
            window.close();
        }

    } else {
        // close the window in case there are no errors
        window.close();
    }
}

bootstrap({
    initializeWorker: (params) =>
        import('./index.worker').then(({ initialize }) => initialize(params)),
    initializeUI: (params) =>
        import('./index.ui').then(({ initialize }) => initialize(params)),
});
