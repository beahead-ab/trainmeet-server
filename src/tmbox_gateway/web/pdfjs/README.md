# pdf.js (Mozilla) 6.4.299, legacy build

Används av Inställningar → Lokförare för att läsa texten ur köruppdragens PDF
i administratörens webbläsare. Servern själv tolkar ingen PDF.

Källa: npm-paketet `pdfjs-dist@6.4.299`, filerna `legacy/build/pdf.min.mjs`
och `legacy/build/pdf.worker.min.mjs`, oförändrade. Licens: Apache-2.0
(`LICENSE-pdfjs.txt`).

Uppdatera: `npm pack pdfjs-dist@<version>`, kopiera samma två filer hit och
ändra versionen här och i `lokforaren-koruppdrag.js`.
