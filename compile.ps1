# Tested on Python 3.12.9

$BASE_PATH="C:\Users\Admin\Documents\work\projects\VSCodeProjects\PromptGen"
$PACKAGE_PATH="$BASE_PATH\.conda\Lib\site-packages\"
$UPX_PATH="C:\Users\Admin\Documents\work\projects\upx-4.2.4-win64\upx-4.2.4-win64"

Set-Location -Path "$BASE_PATH"

conda activate

conda activate "$BASE_PATH\.conda"

black PromptGen.py

nuitka --onefile --standalone --windows-console-mode=disable --file-version=1.0.2 --product-version=1.0.2 --file-description="LLM Prompt Generator" --product-name="PromptGen" --copyright="© 2025 Flaming Water" --windows-icon-from-ico="$BASE_PATH\docs\icon.ico" --include-data-dir="$BASE_PATH\docs=docs" --enable-plugin=tk-inter --include-data-dir="$PACKAGE_PATH\customtkinter=customtkinter" --include-data-dir="$PACKAGE_PATH\CTkMessagebox=CTkMessagebox" --include-data-dir="$PACKAGE_PATH\ctkcomponents=ctkcomponents" --output-dir="$BASE_PATH\main" --enable-plugin=upx --upx-binary="$UPX_PATH" --lto=yes --clang --remove-output PromptGen.py

Start-Sleep -Seconds 3