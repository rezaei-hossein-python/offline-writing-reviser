# Third-party notices

Offline Writing Reviser includes or interoperates with the following
third-party components. This notice is informational and does not replace the
license files shipped with each private runtime.

## Eclipse Temurin 17

The application installer includes an unmodified Eclipse Temurin Java runtime.
Temurin is distributed under GPLv2 with the Classpath Exception and additional
licenses for bundled components. The complete `legal`, `NOTICE`, and release
metadata supplied by Eclipse Adoptium are included under `runtime/java`.

Source and license information:
https://adoptium.net/ and https://projects.eclipse.org/projects/adoptium.temurin

## LanguageTool 6.6

The application installer includes the unmodified LanguageTool 6.6 standalone
distribution. LanguageTool core is licensed under LGPL-2.1-or-later; language
resources and dependencies may carry their own compatible notices. The
distribution's `COPYING.txt`, `README.md`, and `third-party-licenses` directory
are included under `runtime/languagetool`.

Source and license information:
https://github.com/languagetool-org/languagetool

## Ollama

Ollama is not bundled into the application directory. The bootstrap installer
reuses a compatible existing installation or, with user consent and an
internet connection, downloads the official Windows installer from Ollama.
Ollama is licensed under the MIT License.

Source and license information:
https://github.com/ollama/ollama

## Gemma 3

The `gemma3:4b` model is not bundled. With user consent, it is downloaded by
the locally installed Ollama runtime. Gemma is subject to the Gemma Terms of
Use and Prohibited Use Policy presented by its distributor.

Terms:
https://ai.google.dev/gemma/terms
