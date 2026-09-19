#![allow(linker_messages)]

#[tauri::command]
fn open_download_folder() -> Result<(), String> {
    let folder = std::env::var_os("CONTENT_FACTORY_EXPORT_ROOT")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("CONTENT_FACTORY_USER_ROOT").map(|root| std::path::PathBuf::from(root).join("exports")))
        .unwrap_or_else(|| std::path::PathBuf::from(r"E:\Codex工作盘\artifacts\latest\男装编剪器下载"));
    if !folder.is_dir() { return Err("下载文件夹不存在".into()); }
    std::process::Command::new("explorer.exe").arg(folder).spawn().map_err(|_| "无法打开下载文件夹".to_string())?;
    Ok(())
}

#[tauri::command]
fn open_service_dashboard() -> Result<(), String> {
    // Fixed user-approved public URL; neither callers nor model output supply arguments.
    std::process::Command::new("explorer.exe").arg("https://apikey.fun/dashboard")
        .spawn().map_err(|_| "无法打开服务商后台".to_string())?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut context = tauri::generate_context!();
    if let Ok(value) = std::env::var("CONTENT_FACTORY_API_PORT") {
        if let Ok(port) = value.parse::<u16>() {
            if port >= 1024 {
                if let Some(window) = context.config_mut().app.windows.first_mut() {
                    window.url = tauri::WebviewUrl::App(format!("index.html?cfPort={port}").into());
                }
            }
        }
    }
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![open_download_folder, open_service_dashboard])
        .run(context).expect("failed to run the content factory desktop application");
}
