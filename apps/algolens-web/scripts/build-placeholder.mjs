import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("dist", { recursive: true });
writeFileSync("dist/index.html", "<!doctype html><title>algolens-web placeholder</title>\n");
console.log("algolens-web build placeholder");
