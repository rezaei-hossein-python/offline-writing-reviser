# Third-party notices

Offline Writing Reviser includes or interoperates with the following
third-party components. This notice is informational and does not replace the
license and notice files shipped with the private runtime distributions.

## Eclipse Temurin 17

The application includes an unmodified Eclipse Temurin 17.0.20+8 x64 JRE for
its private LanguageTool process. Temurin is distributed under GPLv2 with the
Classpath Exception, with additional licences for bundled components. The
complete `legal` directory and release metadata supplied by Eclipse Adoptium
are retained under `runtime/java`.

Source and license information:
https://adoptium.net/ and https://projects.eclipse.org/projects/adoptium.temurin

## LanguageTool 6.6

The application includes the unmodified LanguageTool 6.6 standalone
distribution for fully offline English mechanical correction. LanguageTool
core is licensed under LGPL-2.1-or-later. Its `COPYING.txt`, `README.md`, and
`third-party-licenses` directory are retained under `runtime/languagetool`.

Source and license information:
https://github.com/languagetool-org/languagetool

## Ollama

Ollama is not bundled into the application directory. The bootstrap installer
reuses a compatible existing installation or, with user consent and an
internet connection, downloads the official Windows installer from Ollama.
Ollama is licensed under the MIT License.

Source and license information:
https://github.com/ollama/ollama

## Qwen 3

The `qwen3:1.7b` model is not bundled. With user consent, it is downloaded by
the locally installed Ollama runtime. The model metadata distributed through
Ollama identifies Qwen 3 as licensed under the Apache License 2.0.

Source and license information:
https://github.com/QwenLM/Qwen3 and https://www.apache.org/licenses/LICENSE-2.0
