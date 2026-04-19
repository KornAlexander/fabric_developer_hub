declare module '*.module.scss' {
    const classes: { [key: string]: string };
    export = classes;
}

// PDF.js ships ESM entry points without type declarations that match our
// module setup. We use them with dynamic imports and `any` typing, so a
// minimal module declaration keeps TypeScript happy.
declare module 'pdfjs-dist/build/pdf.mjs';
