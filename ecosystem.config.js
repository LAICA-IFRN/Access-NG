// PM2 ecosystem — Access NG
// Uso:
//   pm2 start ecosystem.config.js        (primeira vez)
//   pm2 reload ecosystem.config.js       (zero-downtime reload)
//   pm2 save && pm2 startup              (persistir após reboot)
//
// Se usar virtualenv, troque o interpreter pelo caminho completo:
//   interpreter: '/home/user/Access-NG/venv/bin/python3'

module.exports = {
  apps: [
    {
      name: 'access-ng-api',
      script: 'api.py',
      interpreter: 'python3',
      cwd: './Sistema',
      watch: false,
      env: {
        FLASK_ENV: 'production',
        FLASK_DEBUG: '0',
      },
      error_file: '../logs/api-error.log',
      out_file: '../logs/api-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
  ],
};
