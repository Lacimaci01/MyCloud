# MyCloud
# MyCloud
# Default admin user
 Username: root
 Password: rootadmin0
MyCloud is a lightweight, self-hosted file storage platform built with Python and Flask. It provides a modern web interface for storing, organizing, previewing, searching, and sharing files from your own computer or server.

MyCloud is designed as a simple self-hosted alternative for people who want control over where their files are stored.

## Features

### File Management

- File uploads and downloads
- Folder creation and navigation
- Drag-and-drop uploads
- Upload progress indicator
- File search
- File deletion
- File metadata
- Image previews
- Storage usage statistics
- Per-user storage quotas

### Sharing

- File sharing between users
- Public share links
- Share link management
- Private files by default

### Accounts

- User authentication
- Secure password hashing
- Password changes
- Administrator dashboard
- Admin-only user creation
- Multiple administrators
- User management
- Individual storage quotas
- Separate private storage for each user
- Two-factor authentication

### Interface

- Modern web interface
- Dark mode
- Responsive design
- Mobile-friendly layout
- File and folder browser
- Storage usage indicator
- Image previews
- Drag-and-drop interface

### Security

- Password hashing
- User-specific file permissions
- Protected file downloads
- Private storage
- Two-factor authentication
- Administrator permissions
- Activity history
- Secure public share identifiers

## Activity History

MyCloud keeps an activity history for important account and file operations, including actions such as:

- File uploads
- File downloads
- File deletions
- Folder creation
- File sharing
- Account changes
- Administrator actions

This makes it easier for users and administrators to understand what has happened on the server.

## File Sharing

Files can be shared with other MyCloud users without making them publicly accessible.

Public share links can also be created when a file needs to be shared with someone who does not have a MyCloud account.

## File Search

The built-in search system makes it possible to quickly locate files and folders without manually navigating through the entire directory structure.

## Image Previews

Supported images can be previewed directly from the browser without downloading them first.

## Storage Quotas

Administrators can configure an individual storage limit for every user.

MyCloud tracks how much storage each account currently uses and prevents uploads when the configured limit has been reached.

## Administration

Administrators can manage the MyCloud server through the web interface.

Administrators can:

- Create users
- Delete users
- Change storage quotas
- Promote users to administrators
- Remove administrator privileges
- Review activity
- Manage accounts

Multiple administrator accounts are supported.

## Two-Factor Authentication

Accounts can optionally use two-factor authentication for additional protection.

A password alone is therefore not necessarily enough to access a protected account when 2FA is enabled.

## Dark Mode

MyCloud includes both light and dark interfaces for comfortable use in different environments.

## Mobile Support

The interface is responsive and designed to work on:

- Desktop computers
- Laptops
- Tablets
- Smartphones

No separate mobile application is required to access the web interface.

## Requirements

- Python 3
- Flask

Install the required Python packages:

```bash id="a2d1mx"
pip install flask
```

## Running MyCloud

Start the server:

```bash id="z2z1qy"
python app.py
```

MyCloud runs on port `8000` by default.

On the host computer:

```text id="63a1jv"
http://127.0.0.1:8000
```

For another device on the same network, find the local IPv4 address of the server.

On Windows:

```bash id="2cvq68"
ipconfig
```

Then connect using:

```text id="f02q8k"
http://YOUR_LOCAL_IP:8000
```

For example:

```text id="qv8hcn"
http://98.165.23.1:8000
```

## Default Administrator

The initial administrator account is:

```text id="akp15h"
Username: root
Password: rootadmin0
```

Change the default password before using MyCloud on an untrusted network.

## Data Storage

MyCloud stores its data locally.

```text id="w60mv4"
MyCloud/
├── app.py
├── cloud.db
└── storage/
```

`cloud.db` contains account information, password hashes, quotas, file metadata, sharing information, and other application data.

The `storage` directory contains the uploaded files.

## Privacy

Files are stored on the machine running MyCloud instead of automatically being uploaded to a third-party cloud provider.

The server administrator is responsible for protecting the server, database, backups, and storage directory.

## Security Notice

MyCloud is primarily a personal, educational, and self-hosting project.

The Flask development server should not be directly exposed to the public Internet.

A public deployment should use additional protections such as:

- HTTPS
- A production WSGI server
- Reverse proxy
- Secure persistent secret keys
- CSRF protection
- Rate limiting
- Secure cookies
- Regular backups
- Strong administrator passwords
- Firewall configuration
- Regular dependency updates

## Disclaimer

MyCloud is an independent self-hosted project and is not intended to provide the same security, reliability, redundancy, or feature guarantees as large production cloud platforms without additional infrastructure and security configuration.

Always maintain backups of important files.

## License

Add an open-source license such as the MIT License if you want other people to freely use, modify, and contribute to the project.
